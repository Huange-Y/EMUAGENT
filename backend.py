"""
Backend abstraction layer for firmware validation.

Provides a unified interface that vulnagent can use to choose between:
- **DirectHardwareBackend**: connect directly to a real device IP
- **EmulationAgentBackend**: use the emulation agent server (QEMU-based)
- **StaticOnlyBackend**: fallback to pure static analysis

The :class:`BackendFactory` auto-discovers and selects the best available
backend based on target type and agent reachability.
"""

from __future__ import annotations

import logging
import re
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class ValidationResult(str, Enum):
    """Result of a firmware validation attempt."""

    VERIFIED = "verified"          # Service confirmed running and responding
    EMULATED = "emulated"          # Service started but not yet verified
    STATIC_ONLY = "static_only"    # Cannot emulate, static analysis only
    FAILED = "failed"              # Emulation or connection failed
    PENDING = "pending"            # Not yet attempted


@dataclass
class ProbeResult:
    """Result of probing a network service.

    Attributes:
        reachable: Whether the service responded to the probe.
        protocol: Detected protocol (``"http"``, ``"telnet"``, ``"tcp"``, ``"unknown"``).
        port: Port that was probed.
        banner: Service banner string, if available.
        http_status: HTTP status code, if the service is an HTTP server.
        http_headers: HTTP response headers, if applicable.
        latency_ms: Round-trip latency in milliseconds.
        error: Error message if the probe failed.
    """

    reachable: bool
    protocol: str = "tcp"
    port: int = 0
    banner: Optional[str] = None
    http_status: Optional[int] = None
    http_headers: Optional[Dict[str, str]] = None
    latency_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict for JSON output."""
        d: Dict[str, Any] = {
            "reachable": self.reachable,
            "protocol": self.protocol,
            "port": self.port,
            "banner": self.banner,
            "http_status": self.http_status,
            "latency_ms": round(self.latency_ms, 1),
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class EmulationResult:
    """Full result of a firmware emulation attempt.

    Attributes:
        firmware_path: Path to the firmware that was validated.
        rootfs_id: Identifier of the uploaded rootfs.
        arch: Detected CPU architecture.
        services: List of service dicts that were started.
        probe_results: Probe results for each service.
        validation: Overall validation outcome.
        errors: Any errors encountered during the pipeline.
        duration_ms: Total wall-clock time for the pipeline in milliseconds.
    """

    firmware_path: str = ""
    rootfs_id: Optional[str] = None
    arch: Optional[str] = None
    services: List[Dict[str, Any]] = field(default_factory=list)
    probe_results: List[ProbeResult] = field(default_factory=list)
    validation: ValidationResult = ValidationResult.PENDING
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def is_verified(self) -> bool:
        """Return True if validation was successful."""
        return self.validation == ValidationResult.VERIFIED

    @property
    def is_emulated(self) -> bool:
        """Return True if service was started (verified or emulated)."""
        return self.validation in (ValidationResult.VERIFIED, ValidationResult.EMULATED)

    @property
    def has_any_reachable(self) -> bool:
        """Return True if at least one probe succeeded."""
        return any(p.reachable for p in self.probe_results)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "firmware_path": self.firmware_path,
            "rootfs_id": self.rootfs_id,
            "arch": self.arch,
            "validation": self.validation.value,
            "is_verified": self.is_verified,
            "is_emulated": self.is_emulated,
            "service_count": len(self.services),
            "probe_results": [p.to_dict() for p in self.probe_results],
            "errors": self.errors,
            "duration_ms": round(self.duration_ms, 1),
        }


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class FirmwareValidationBackend(ABC):
    """Abstract base class for firmware validation backends.

    Subclasses must implement :meth:`validate` and :meth:`is_available`.
    """

    @abstractmethod
    def validate(
        self,
        firmware_path: str,
        target_binary: Optional[str] = None,
    ) -> EmulationResult:
        """Validate firmware by attempting to run and probe the target binary.

        Args:
            firmware_path: Path to the firmware file or rootfs.
            target_binary: Name of the binary to target (e.g. ``"httpd"``).
                When None, interesting binaries are auto-discovered.

        Returns:
            An :class:`EmulationResult` summarising the outcome.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether this backend is currently reachable and usable.

        Returns:
            ``True`` if the backend can be used for validation.
        """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend name."""


# ---------------------------------------------------------------------------
# DirectHardwareBackend
# ---------------------------------------------------------------------------


class DirectHardwareBackend(FirmwareValidationBackend):
    """Backend that connects directly to a real device at a target IP.

    This is used when the user specifies ``--target <ip>``.  The backend
    probes the real device directly without attempting any emulation.

    Parameters:
        target_host: IP address or hostname of the target device.
        target_port: TCP port to probe (default 80 for web-based devices).
        timeout: TCP connect timeout in seconds.
    """

    backend_name = "direct_hardware"

    def __init__(
        self,
        target_host: str,
        target_port: int = 80,
        timeout: float = 5.0,
    ) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check reachability by attempting a TCP connect."""
        logger.debug(
            "Checking direct hardware availability: %s:%d",
            self.target_host,
            self.target_port,
        )
        try:
            sock = socket.create_connection(
                (self.target_host, self.target_port),
                timeout=self.timeout,
            )
            sock.close()
            logger.info(
                "Direct hardware backend available at %s:%d",
                self.target_host,
                self.target_port,
            )
            return True
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "Direct hardware backend NOT available at %s:%d: %s",
                self.target_host,
                self.target_port,
                exc,
            )
            return False

    def validate(
        self,
        firmware_path: str,
        target_binary: Optional[str] = None,
    ) -> EmulationResult:
        """Probe the real device directly.

        The *firmware_path* and *target_binary* arguments are accepted for
        interface compatibility but are not used — the backend simply probes
        the configured target address.

        Returns:
            An :class:`EmulationResult` with probe results for common ports.
        """
        start = time.monotonic()
        logger.info(
            "DirectHardwareBackend: probing %s:%d",
            self.target_host,
            self.target_port,
        )

        result = EmulationResult(
            firmware_path=firmware_path,
            validation=ValidationResult.PENDING,
        )

        # Probe common IoT service ports
        ports_to_probe: set[int] = {self.target_port}
        ports_to_probe.update([80, 443, 8080, 8443, 23, 21, 22])

        for port in sorted(ports_to_probe):
            probe = self._probe_port(self.target_host, port)
            result.probe_results.append(probe)
            if not probe.reachable and probe.error:
                result.errors.append(f"port {port}: {probe.error}")

        # Determine overall validation
        if any(p.reachable for p in result.probe_results):
            result.validation = ValidationResult.VERIFIED
        else:
            result.validation = ValidationResult.FAILED

        result.duration_ms = (time.monotonic() - start) * 1000.0
        return result

    def _probe_port(self, host: str, port: int) -> ProbeResult:
        """Probe a single TCP port to determine service type and reachability.

        Attempts to detect HTTP by sending a HEAD request; falls back to a
        simple TCP connect + banner read.
        """
        t0 = time.monotonic()
        try:
            sock = socket.create_connection((host, port), timeout=self.timeout)
            latency = (time.monotonic() - t0) * 1000.0

            # Try HTTP detection first
            try:
                sock.settimeout(2.0)
                request = (
                    f"HEAD / HTTP/1.0\r\n"
                    f"Host: {host}\r\n"
                    f"User-Agent: VulnAgent/1.0\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
                sock.sendall(request.encode("ascii", errors="replace"))
                response = b""
                while True:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        response += chunk
                    except socket.timeout:
                        break

                response_str = response.decode("utf-8", errors="replace")

                # Parse HTTP status line
                if response_str.startswith("HTTP/"):
                    lines = response_str.split("\r\n")
                    status_parts = lines[0].split(" ", 2)
                    http_status = int(status_parts[1]) if len(status_parts) >= 2 else None

                    headers: Dict[str, str] = {}
                    for line in lines[1:]:
                        if ":" in line:
                            key, _, val = line.partition(":")
                            headers[key.strip().lower()] = val.strip()
                        elif line == "":
                            break

                    sock.close()
                    return ProbeResult(
                        reachable=True,
                        protocol="http",
                        port=port,
                        banner=headers.get("server"),
                        http_status=http_status,
                        http_headers=headers if headers else None,
                        latency_ms=latency,
                    )

                # Not HTTP — treat response as a banner
                banner = response_str.strip()[:200] if response_str.strip() else None
                sock.close()
                return ProbeResult(
                    reachable=True,
                    protocol="tcp",
                    port=port,
                    banner=banner,
                    latency_ms=latency,
                )

            except (socket.timeout, OSError):
                # HTTP detection failed but TCP connect succeeded
                sock.close()
                return ProbeResult(
                    reachable=True,
                    protocol="tcp",
                    port=port,
                    latency_ms=latency,
                )

        except socket.timeout:
            return ProbeResult(
                reachable=False,
                port=port,
                error=f"Connection timed out after {self.timeout}s",
            )
        except ConnectionRefusedError:
            return ProbeResult(
                reachable=False,
                port=port,
                error="Connection refused",
            )
        except OSError as exc:
            return ProbeResult(
                reachable=False,
                port=port,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# EmulationAgentBackend
# ---------------------------------------------------------------------------


class EmulationAgentBackend(FirmwareValidationBackend):
    """Backend that uses the Emulation Agent server to run firmware binaries.

    Uploads the firmware to the agent, detects the architecture, starts the
    target binary under QEMU user-mode emulation, and probes the resulting
    service.

    Parameters:
        agent_host: Hostname or IP of the agent server.
        agent_port: TCP port the agent server listens on.
        timeout: HTTP request timeout in seconds.
    """

    backend_name = "emulation_agent"

    # Binaries to try when auto-discovering
    _AUTO_DISCOVER_BINARIES: Tuple[str, ...] = (
        "httpd",
        "lighttpd",
        "nginx",
        "boa",
        "goahead",
        "mini_httpd",
        "uhttpd",
        "telnetd",
        "dropbear",
        "sshd",
        "dnsmasq",
        "snmpd",
        "ftpd",
        "miniupnpd",
    )

    def __init__(
        self,
        agent_host: str = "your-vm-ip",
        agent_port: int = 9100,
        timeout: int = 60,
    ) -> None:
        self.agent_host = agent_host
        self.agent_port = agent_port
        self.timeout = timeout
        self._client: Any = None  # Lazy-loaded EmulationAgentClient

    @property
    def client(self) -> Any:
        """Lazy-load the HTTP client to avoid import-time circular deps."""
        if self._client is None:
            try:
                from emulation_agent.client import EmulationAgentClient
            except ModuleNotFoundError:
                from client import EmulationAgentClient

            self._client = EmulationAgentClient(
                host=self.agent_host,
                port=self.agent_port,
                timeout=self.timeout,
            )
        return self._client

    def is_available(self) -> bool:
        """Check agent server reachability via the health endpoint."""
        try:
            response = self.client.health()
            available = response.get("status") in ("ok", "degraded")
            if available:
                logger.info(
                    "Emulation agent available at %s:%d",
                    self.agent_host,
                    self.agent_port,
                )
            else:
                logger.warning(
                    "Emulation agent responded but status=%s",
                    response.get("status"),
                )
            return available
        except Exception as exc:
            logger.warning(
                "Emulation agent NOT available at %s:%d: %s",
                self.agent_host,
                self.agent_port,
                exc,
            )
            return False

    def validate(
        self,
        firmware_path: str,
        target_binary: Optional[str] = None,
    ) -> EmulationResult:
        """Full validation pipeline via the emulation agent.

        1. Upload rootfs
        2. Detect architecture
        3. Start service (auto-discover if no binary specified)
        4. Probe the service

        Args:
            firmware_path: Local path to the firmware file.
            target_binary: Binary to target. If None, auto-discovers from a
                list of commonly interesting embedded binaries.

        Returns:
            An :class:`EmulationResult` with full pipeline details.
        """
        start = time.monotonic()
        logger.info(
            "EmulationAgentBackend: validating %s (target_binary=%s)",
            firmware_path,
            target_binary,
        )

        result = EmulationResult(
            firmware_path=firmware_path,
            validation=ValidationResult.PENDING,
        )

        # ---- Step 1: Upload rootfs ----
        try:
            upload_resp = self.client.upload_rootfs(file_path=firmware_path)
            result.rootfs_id = upload_resp.get("rootfs_id", "")
            if not result.rootfs_id:
                result.errors.append(
                    f"Upload failed: no rootfs_id in response: {upload_resp}"
                )
                result.validation = ValidationResult.FAILED
                result.duration_ms = (time.monotonic() - start) * 1000.0
                return result
            logger.info("Uploaded rootfs: %s", result.rootfs_id)
        except Exception as exc:
            result.errors.append(f"Upload error: {exc}")
            result.validation = ValidationResult.FAILED
            result.duration_ms = (time.monotonic() - start) * 1000.0
            return result

        # ---- Step 2: Detect architecture ----
        try:
            arch_resp = self.client.detect_arch(result.rootfs_id)
            result.arch = arch_resp.get("arch", "unknown")
            logger.info("Detected architecture: %s", result.arch)
        except Exception as exc:
            logger.warning("Architecture detection failed: %s", exc)
            result.errors.append(f"Arch detection warning: {exc}")
            result.arch = "unknown"

        # ---- Step 3: Try pipeline via client's convenience method ----
        try:
            pipeline_result = self.client.emulate_and_probe(
                firmware_path=firmware_path,
                target_binary=target_binary,
            )

            # Extract service info
            service_id = pipeline_result.get("service_id")
            binary = pipeline_result.get("binary")
            if service_id and binary:
                result.services.append({
                    "service_id": service_id,
                    "binary": binary,
                    "status": pipeline_result.get("status", "unknown"),
                })

            # Extract probe result
            probe_data = pipeline_result.get("probe_result")
            if probe_data and isinstance(probe_data, dict):
                result.probe_results.append(ProbeResult(
                    reachable=bool(probe_data.get("reachable", False)),
                    protocol=str(probe_data.get("protocol", "tcp")),
                    port=probe_data.get("port", self.agent_port),
                    banner=probe_data.get("banner"),
                    http_status=probe_data.get("http_status"),
                    latency_ms=probe_data.get("latency_ms", 0.0),
                ))

            # Map pipeline status to validation result
            status = pipeline_result.get("status", "failed")
            if status == "verified":
                result.validation = ValidationResult.VERIFIED
            elif status == "emulated":
                result.validation = ValidationResult.EMULATED
            else:
                result.validation = ValidationResult.FAILED

            if pipeline_result.get("error"):
                result.errors.append(str(pipeline_result["error"]))

        except Exception as exc:
            result.errors.append(f"Pipeline error: {exc}")
            result.validation = ValidationResult.FAILED

        result.duration_ms = (time.monotonic() - start) * 1000.0
        logger.info(
            "EmulationAgentBackend complete: %s (%.0fms)",
            result.validation.value,
            result.duration_ms,
        )
        return result


# ---------------------------------------------------------------------------
# StaticOnlyBackend
# ---------------------------------------------------------------------------


class StaticOnlyBackend(FirmwareValidationBackend):
    """Fallback backend that marks validation as static-only.

    This backend is always available and is used when neither direct hardware
    access nor the emulation agent are reachable.  It signals to vulnagent
    that only static analysis (pattern matching, binary inspection) should
    be performed.
    """

    backend_name = "static_only"

    def __init__(self, reason: str = "No emulation backend available") -> None:
        self._reason = reason

    def is_available(self) -> bool:
        """Always returns True — static analysis is always possible."""
        return True

    def validate(
        self,
        firmware_path: str,
        target_binary: Optional[str] = None,
    ) -> EmulationResult:
        """Return a static-only result immediately.

        No emulation or probing is attempted.
        """
        start = time.monotonic()
        logger.info(
            "StaticOnlyBackend: marking %s as STATIC_ONLY (%s)",
            firmware_path,
            self._reason,
        )
        result = EmulationResult(
            firmware_path=firmware_path,
            rootfs_id="static-only",
            arch="unknown",
            validation=ValidationResult.STATIC_ONLY,
            errors=[f"Static only: {self._reason}"],
        )
        result.duration_ms = (time.monotonic() - start) * 1000.0
        return result


# ---------------------------------------------------------------------------
# BackendFactory
# ---------------------------------------------------------------------------


class BackendFactory:
    """Auto-discover and select the best available firmware validation backend.

    The factory implements a priority-ordered discovery algorithm:

    1. If *target* looks like an IP address or hostname, use
       :class:`DirectHardwareBackend`.
    2. Otherwise, if ``emulation.mode`` is not ``"off"`` and the emulation
       agent is reachable, use :class:`EmulationAgentBackend`.
    3. If neither is available, fall back to :class:`StaticOnlyBackend`.

    Usage::

        backend = BackendFactory.create("192.168.1.1", {"mode": "auto"})
        if backend.is_available():
            result = backend.validate("firmware.bin")

    Or discover all available backends::

        backends = BackendFactory.discover_backends({"mode": "auto"})
    """

    @staticmethod
    def create(
        target: str,
        emulation_config: Optional[Dict[str, Any]] = None,
    ) -> FirmwareValidationBackend:
        """Select the best backend for the given *target*.

        Args:
            target: The target specification.  If it looks like an IP or
                hostname, a :class:`DirectHardwareBackend` is preferred.
                Otherwise, the emulation agent is tried.
            emulation_config: Optional dictionary with emulation settings
                (see ``settings.yaml`` ``emulation:`` section).  Keys used:

                - ``mode``: ``"auto"``, ``"agent"``, ``"direct"``, or ``"off"``
                - ``agent_host``: IP of the emulation agent
                - ``agent_port``: port of the emulation agent
                - ``ssh_tunnel``: enable SSH tunnel fallback
                - ``ssh_host``: SSH tunnel host
                - ``ssh_port``: SSH tunnel port

        Returns:
            A configured :class:`FirmwareValidationBackend` instance.
        """
        config = emulation_config or {}

        # If mode is explicitly "off", go straight to static
        mode = config.get("mode", "auto").lower()
        if mode == "off":
            logger.info("Emulation mode is 'off' — using StaticOnlyBackend")
            return StaticOnlyBackend(reason="Emulation disabled in config")

        # Direct mode or network target: use DirectHardwareBackend
        if mode == "direct" or BackendFactory._is_network_target(target):
            direct_port = int(config.get("direct_port", 80))
            backend = DirectHardwareBackend(
                target_host=target,
                target_port=direct_port,
            )
            if backend.is_available():
                logger.info("Selected DirectHardwareBackend for %s", target)
                return backend
            logger.warning(
                "DirectHardwareBackend not available for %s — falling back",
                target,
            )

        # Try emulation agent (direct connection)
        if mode in ("auto", "agent"):
            agent_host = config.get("agent_host", "your-vm-ip")
            agent_port = int(config.get("agent_port", 9100))
            agent_backend = EmulationAgentBackend(
                agent_host=agent_host,
                agent_port=agent_port,
            )
            if agent_backend.is_available():
                logger.info(
                    "Selected EmulationAgentBackend (%s:%d)",
                    agent_host,
                    agent_port,
                )
                return agent_backend

            # Try SSH tunnel fallback
            if config.get("ssh_tunnel") and mode == "auto":
                ssh_host = config.get("ssh_host", "localhost")
                ssh_port = int(config.get("ssh_port", 2222))
                tunnel_backend = EmulationAgentBackend(
                    agent_host=ssh_host,
                    agent_port=agent_port,
                )
                if tunnel_backend.is_available():
                    logger.info(
                        "Selected EmulationAgentBackend via SSH tunnel (%s:%d)",
                        ssh_host,
                        agent_port,
                    )
                    return tunnel_backend

        # Final fallback
        logger.info("No dynamic backend available — using StaticOnlyBackend")
        return StaticOnlyBackend(
            reason=f"No reachable backend for target '{target}'"
        )

    @staticmethod
    def discover_backends(
        emulation_config: Optional[Dict[str, Any]] = None,
    ) -> List[FirmwareValidationBackend]:
        """Discover all available backends in priority order.

        This returns backends that pass
        :meth:`~FirmwareValidationBackend.is_available`, with
        :class:`StaticOnlyBackend` always at the end.

        Args:
            emulation_config: Optional emulation settings dict.

        Returns:
            List of available backends, highest priority first.  Always
            contains at least :class:`StaticOnlyBackend`.
        """
        config = emulation_config or {}
        mode = config.get("mode", "auto").lower()
        backends: List[FirmwareValidationBackend] = []

        if mode == "off":
            backends.append(StaticOnlyBackend(reason="Emulation disabled in config"))
            return backends

        # Emulation agent — direct
        agent_host = config.get("agent_host", "your-vm-ip")
        agent_port = int(config.get("agent_port", 9100))
        agent_backend = EmulationAgentBackend(
            agent_host=agent_host,
            agent_port=agent_port,
        )
        if agent_backend.is_available():
            backends.append(agent_backend)

        # Emulation agent — SSH tunnel
        if config.get("ssh_tunnel"):
            ssh_host = config.get("ssh_host", "localhost")
            ssh_port = int(config.get("ssh_port", 2222))
            tunnel_backend = EmulationAgentBackend(
                agent_host=ssh_host,
                agent_port=agent_port,
            )
            if tunnel_backend.is_available():
                if not backends or backends[-1].backend_name != "emulation_agent":
                    backends.append(tunnel_backend)

        # Static is always last
        backends.append(StaticOnlyBackend())

        logger.info(
            "Discovered %d backends: %s",
            len(backends),
            ", ".join(b.backend_name for b in backends),
        )
        return backends

    @staticmethod
    def _is_network_target(target: str) -> bool:
        """Check if a target string looks like a network address.

        Returns True for IPv4 addresses, IPv6 addresses, and hostnames.
        Returns False for file paths and firmware filenames.
        """
        # IPv4 with optional port
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?$", target):
            return True
        # IPv6
        if ":" in target and not target.startswith("http"):
            try:
                socket.inet_pton(socket.AF_INET6, target)
                return True
            except (socket.error, OSError):
                pass
        # Hostname: contains dot, no slashes, not a firmware extension
        if "." in target and "/" not in target and "\\" not in target:
            if not target.endswith((".bin", ".img", ".tar.gz", ".zip", ".tgz")):
                return True
        # localhost
        if target in ("localhost", "::1", "127.0.0.1"):
            return True
        return False


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

__all__ = [
    "FirmwareValidationBackend",
    "DirectHardwareBackend",
    "EmulationAgentBackend",
    "StaticOnlyBackend",
    "BackendFactory",
    "ValidationResult",
    "EmulationResult",
    "ProbeResult",
]
