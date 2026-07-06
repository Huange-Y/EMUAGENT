"""
HTTP client for communicating with the Emulation Agent server.

Provides a Pythonic interface to all API endpoints with automatic retries,
exponential backoff, connection timeout handling, and structured logging.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Recognized firmware / rootfs extensions and their format labels
_EXTENSION_FORMAT_MAP: Dict[str, str] = {
    ".bin": "raw",
    ".img": "raw",
    ".tar.gz": "tar.gz",
    ".tgz": "tar.gz",
    ".tar.bz2": "tar.bz2",
    ".tbz2": "tar.bz2",
    ".tar.xz": "tar.xz",
    ".txz": "tar.xz",
    ".tar": "tar",
    ".zip": "zip",
    ".squashfs": "squashfs",
    ".sqfs": "squashfs",
    ".cpio": "cpio",
    ".cpio.gz": "cpio.gz",
    ".cramfs": "cramfs",
}

# Binary names that are commonly interesting for vulnerability research
_INTERESTING_BINARIES: List[str] = [
    "httpd",
    "lighttpd",
    "nginx",
    "apache2",
    "boa",
    "goahead",
    "mini_httpd",
    "uhttpd",
    "telnetd",
    "dropbear",
    "sshd",
    "dnsmasq",
    "upnpd",
    "snmpd",
    "ftpd",
    "smbd",
    "nmbd",
    "miniupnpd",
]


def _detect_format(file_path: Optional[str] = None, url: Optional[str] = None) -> str:
    """Auto-detect the format of a firmware file from its extension.

    Args:
        file_path: Local path to the file.
        url: Remote URL to the file.

    Returns:
        Format string suitable for the server (e.g. ``"tar.gz"``, ``"raw"``).

    Raises:
        ValueError: If neither *file_path* nor *url* is provided, or the format
            cannot be determined.
    """
    if file_path is not None:
        name = os.path.basename(file_path).lower()
    elif url is not None:
        name = url.rsplit("?", 1)[0].rsplit("/", 1)[-1].lower()
    else:
        raise ValueError("Either file_path or url must be provided for format detection")

    # Walk from longest to shortest extension for correct matching (e.g. .tar.gz before .gz)
    for ext, fmt in sorted(_EXTENSION_FORMAT_MAP.items(), key=lambda x: -len(x[0])):
        if name.endswith(ext):
            return fmt
    return "auto"


class EmulationAgentClient:
    """HTTP client for the Emulation Agent REST API.

    All methods that talk to the server are wrapped with automatic retry logic
    (3 attempts, exponential backoff) and will raise
    :class:`requests.exceptions.RequestException` on persistent failures.

    Typical usage::

        client = EmulationAgentClient("your-vm-ip", 9100)
        health = client.health()

    Parameters:
        host: Hostname or IP address of the agent server.
        port: TCP port the server listens on.
        timeout: Request timeout in seconds (connect + read).
    """

    def __init__(
        self,
        host: str = "your-vm-ip",
        port: int = 9100,
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

        self.session = requests.Session()

        # Configure retry strategy (3 total attempts with exponential backoff)
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,  # 1s, 2s, 4s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=[
                "HEAD",
                "GET",
                "POST",
                "PUT",
                "DELETE",
                "OPTIONS",
                "TRACE",
            ],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue an HTTP request with timeout and unified error logging.

        Raises:
            requests.exceptions.RequestException: On transport or HTTP errors.
        """
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", (5.0, self.timeout))  # (connect, read)
        logger.debug("%s %s (timeout=%s)", method, url, kwargs["timeout"])
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _post_json(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST JSON to *path* and return the parsed response body."""
        resp = self._request("POST", path, json=data or {})
        return resp.json()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET *path* and return the parsed response body."""
        resp = self._request("GET", path, params=params)
        return resp.json()

    # ------------------------------------------------------------------
    # Core API methods
    # ------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """Check server health.

        ``GET /api/health``

        Returns:
            A dictionary with at least a ``"status"`` key (``"ok"`` when healthy).
        """
        logger.info("Checking agent health at %s", self.base_url)
        return self._get("/api/health")

    def upload_rootfs(
        self,
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        format: str = "auto",
    ) -> Dict[str, Any]:
        """Upload a firmware image or rootfs to the agent.

        ``POST /api/upload_rootfs``

        Args:
            file_path: Local path to the file (multipart upload).
            url: Remote URL to download from (JSON body).
            format: Format hint (``"auto"``, ``"tar.gz"``, ``"zip"``, ``"raw"``, ...).
                Auto-detected from extension when set to ``"auto"``.

        Returns:
            Response dict containing at least a ``"rootfs_id"`` key.
        """
        if file_path is not None:
            if not os.path.isfile(file_path):
                raise FileNotFoundError(f"Firmware file not found: {file_path}")
            detected_fmt = format if format != "auto" else _detect_format(file_path=file_path)
            logger.info(
                "Uploading rootfs from %s (format=%s, size=%s)",
                file_path,
                detected_fmt,
                os.path.getsize(file_path),
            )
            with open(file_path, "rb") as fh:
                resp = self._request(
                    "POST",
                    "/api/upload_rootfs",
                    files={"file": (os.path.basename(file_path), fh)},
                    data={"format": detected_fmt},
                    timeout=(10.0, self.timeout),  # longer connect timeout for uploads
                )
            return resp.json()

        if url is not None:
            fmt = format if format != "auto" else _detect_format(url=url)
            logger.info("Uploading rootfs from URL %s (format=%s)", url, fmt)
            return self._post_json(
                "/api/upload_rootfs",
                {"url": url, "format": fmt},
            )

        raise ValueError("Either file_path or url must be provided")

    def start_service(
        self,
        rootfs_id: str,
        binary_path: Optional[str] = None,
        binary_name: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        port: Optional[int] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Start an emulated service inside a rootfs.

        ``POST /api/start_service``

        Args:
            rootfs_id: The rootfs identifier returned by :meth:`upload_rootfs`.
            binary_path: Full path to the binary inside the rootfs.
            binary_name: Short name of the binary (alternative to *binary_path*).
            args: Command-line arguments for the binary.
            env: Environment variables to set.
            port: Expected listening port (used for health checks).
            timeout: How long to wait for the service to start (seconds).

        Returns:
            Response dict containing ``"service_id"`` and ``"status"``.
        """
        payload: Dict[str, Any] = {
            "rootfs_id": rootfs_id,
            "timeout": timeout,
        }
        if binary_path is not None:
            payload["binary_path"] = binary_path
        if binary_name is not None:
            payload["binary_name"] = binary_name
        if args is not None:
            payload["args"] = args
        if env is not None:
            payload["env"] = env
        if port is not None:
            payload["port"] = port

        logger.info(
            "Starting service: rootfs=%s binary=%s",
            rootfs_id,
            binary_path or binary_name or "(auto)",
        )
        return self._post_json("/api/start_service", payload)

    def stop_service(self, service_id: str) -> Dict[str, Any]:
        """Stop a running emulated service.

        ``POST /api/stop_service/{service_id}``

        Args:
            service_id: Service identifier returned by :meth:`start_service`.

        Returns:
            Response dict confirming the stop.
        """
        logger.info("Stopping service %s", service_id)
        resp = self._request("POST", f"/api/stop_service/{service_id}")
        return resp.json()

    def list_services(self) -> Dict[str, Any]:
        """List all running emulated services.

        ``GET /api/services``

        Returns:
            Response dict with a ``"services"`` key containing a list of services.
        """
        logger.info("Listing running services")
        return self._get("/api/services")

    def probe(
        self,
        host: str,
        port: int,
        protocol: str = "auto",
        timeout: int = 5,
    ) -> Dict[str, Any]:
        """Probe a host:port to determine service reachability.

        ``POST /api/probe``

        Args:
            host: Target hostname or IP.
            port: Target TCP port.
            protocol: Protocol hint (``"auto"``, ``"tcp"``, ``"http"``, ``"telnet"``).
            timeout: Probe timeout in seconds.

        Returns:
            Response dict with ``"reachable"``, ``"protocol"``, and optional
            ``"banner"``/``"http_status"`` fields.
        """
        logger.info("Probing %s:%d (protocol=%s)", host, port, protocol)
        return self._post_json(
            "/api/probe",
            {"host": host, "port": port, "protocol": protocol, "timeout": timeout},
        )

    def exec_command(
        self,
        rootfs_id: str,
        command: str,
        timeout: int = 10,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run an arbitrary command inside an emulated rootfs.

        ``POST /api/exec``

        Uses ``qemu-<arch>-static`` + ``busybox sh -c`` to execute the command.

        Args:
            rootfs_id: Rootfs identifier.
            command: Shell command to run.
            timeout: Maximum execution time in seconds.
            env: Optional environment variables.

        Returns:
            Response dict with ``"stdout"``, ``"stderr"``, and ``"returncode"``.
        """
        logger.info("Executing in rootfs %s: %s", rootfs_id, command)
        payload: Dict[str, Any] = {
            "rootfs_id": rootfs_id,
            "command": command,
            "timeout": timeout,
        }
        if env is not None:
            payload["env"] = env
        return self._post_json("/api/exec", payload)

    def configure_nvram(
        self,
        rootfs_id: str,
        device_type: str = "auto",
        config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Write an NVRAM configuration template for a rootfs.

        ``POST /api/nvram_config``

        This is particularly useful for GoAhead-based web servers that read
        configuration from an MTD-backed NVRAM partition.

        Args:
            rootfs_id: Rootfs identifier.
            device_type: Device type hint for pre-built templates (``"auto"``, ``"tenda"``,
                ``"dlink"``, ``"netgear"``, ``"tp-link"``, ``"generic"``).
            config: Custom key-value NVRAM overrides.

        Returns:
            Response dict confirming the configuration was written.
        """
        payload: Dict[str, Any] = {
            "rootfs_id": rootfs_id,
            "device_type": device_type,
        }
        if config is not None:
            payload["config"] = config
        logger.info("Configuring NVRAM for rootfs %s (device_type=%s)", rootfs_id, device_type)
        return self._post_json("/api/nvram_config", payload)

    def get_service_logs(self, service_id: str, tail: int = 100) -> Dict[str, Any]:
        """Retrieve logs for a specific emulated service.

        ``GET /api/services/{service_id}/logs``

        Args:
            service_id: Service identifier.
            tail: Number of lines to return from the end of the log.

        Returns:
            Response dict with ``"logs"`` (string) and ``"service_id"``.
        """
        logger.info("Fetching logs for service %s (tail=%d)", service_id, tail)
        return self._get(
            f"/api/services/{service_id}/logs",
            params={"tail": tail},
        )

    def detect_arch(self, rootfs_id: str) -> Dict[str, Any]:
        """Detect the CPU architecture of a rootfs.

        ``POST /api/detect_arch``

        Args:
            rootfs_id: Rootfs identifier.

        Returns:
            Response dict with ``"arch"`` (e.g. ``"mipsel"``, ``"armel"``, ``"aarch64"``).
        """
        logger.info("Detecting architecture for rootfs %s", rootfs_id)
        return self._post_json("/api/detect_arch", {"rootfs_id": rootfs_id})

    def delete_rootfs(self, rootfs_id: str) -> Dict[str, Any]:
        """Delete a rootfs and all associated data.

        ``DELETE /api/rootfs/{rootfs_id}``

        Args:
            rootfs_id: Rootfs identifier.

        Returns:
            Response dict confirming deletion.
        """
        logger.info("Deleting rootfs %s", rootfs_id)
        resp = self._request("DELETE", f"/api/rootfs/{rootfs_id}")
        return resp.json()

    # ------------------------------------------------------------------
    # Convenience / workflow methods
    # ------------------------------------------------------------------

    def emulate_and_probe(
        self,
        firmware_path: str,
        target_binary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Full pipeline: upload, detect architecture, start, probe, and return results.

        If *target_binary* is None, the method tries each binary in the
        "interesting" list (httpd, telnetd, dnsmasq, ...) until one starts
        successfully.

        Args:
            firmware_path: Local path to the firmware file.
            target_binary: Name or path of the binary to emulate. Auto-discovered
                when omitted.

        Returns:
            A dictionary summarising the entire pipeline, including
            ``"rootfs_id"``, ``"arch"``, ``"service_id"``, and ``"probe_result"``.
        """
        logger.info("=== emulate_and_probe pipeline: %s ===", firmware_path)

        result: Dict[str, Any] = {
            "firmware_path": firmware_path,
            "rootfs_id": None,
            "arch": None,
            "service_id": None,
            "binary": None,
            "probe_result": None,
            "status": "unknown",
        }

        # Step 1: Upload rootfs
        upload_resp = self.upload_rootfs(file_path=firmware_path)
        rootfs_id = upload_resp.get("rootfs_id")
        if not rootfs_id:
            result["status"] = "upload_failed"
            result["error"] = upload_resp
            return result
        result["rootfs_id"] = rootfs_id

        # Step 2: Detect architecture
        try:
            arch_resp = self.detect_arch(rootfs_id)
            result["arch"] = arch_resp.get("arch", "unknown")
        except requests.RequestException:
            logger.warning("Architecture detection failed; continuing anyway")

        # Step 3: Determine which binary to start
        binaries_to_try: List[str]
        if target_binary is not None:
            binaries_to_try = [target_binary]
        else:
            binaries_to_try = _INTERESTING_BINARIES[:]

        # Step 4: Try to start each candidate binary
        service_id = None
        started_binary = None
        for binary_name in binaries_to_try:
            try:
                start_resp = self.start_service(
                    rootfs_id=rootfs_id,
                    binary_name=binary_name,
                    timeout=20,
                )
                if start_resp.get("status") in ("running", "started"):
                    service_id = start_resp.get("service_id")
                    started_binary = binary_name
                    logger.info("Successfully started %s (service_id=%s)", binary_name, service_id)
                    break
            except requests.RequestException as exc:
                logger.debug("Failed to start %s: %s", binary_name, exc)
                continue

        if service_id is None:
            result["status"] = "no_service_started"
            return result

        result["service_id"] = service_id
        result["binary"] = started_binary

        # Step 5: Probe the service
        probe_result = None
        try:
            probe_result = self.probe(
                host=self.host,
                port=80,  # most embedded web servers run on port 80
                protocol="auto",
                timeout=5,
            )
        except requests.RequestException:
            # Try common alternative ports
            for alt_port in (8080, 443, 23, 21):
                try:
                    probe_result = self.probe(
                        host=self.host,
                        port=alt_port,
                        timeout=5,
                    )
                    if probe_result.get("reachable"):
                        break
                except requests.RequestException:
                    continue

        result["probe_result"] = probe_result
        result["status"] = (
            "verified"
            if (probe_result and probe_result.get("reachable"))
            else "emulated"
        )
        logger.info("=== Pipeline complete: status=%s ===", result["status"])
        return result

    def batch_emulate(
        self,
        firmware_path: str,
        binaries: List[str],
    ) -> List[Dict[str, Any]]:
        """Emulate multiple binaries from the same rootfs.

        The firmware is uploaded once; each binary is started, probed, and
        stopped in sequence.

        Args:
            firmware_path: Local path to the firmware file.
            binaries: List of binary names to emulate (e.g. ``["httpd", "telnetd"]``).

        Returns:
            A list of result dicts, one per binary, each containing
            ``"binary"``, ``"service_id"``, ``"probe_result"``, and ``"status"``.
        """
        logger.info("=== batch_emulate: %d binaries from %s ===", len(binaries), firmware_path)

        # Upload once
        upload_resp = self.upload_rootfs(file_path=firmware_path)
        rootfs_id = upload_resp.get("rootfs_id")
        if not rootfs_id:
            return [
                {
                    "binary": b,
                    "status": "upload_failed",
                    "error": str(upload_resp),
                }
                for b in binaries
            ]

        results: List[Dict[str, Any]] = []
        for binary_name in binaries:
            entry: Dict[str, Any] = {
                "binary": binary_name,
                "rootfs_id": rootfs_id,
                "service_id": None,
                "probe_result": None,
                "status": "unknown",
            }
            try:
                start_resp = self.start_service(rootfs_id=rootfs_id, binary_name=binary_name)
                service_id = start_resp.get("service_id")
                entry["service_id"] = service_id

                if start_resp.get("status") not in ("running", "started"):
                    entry["status"] = "start_failed"
                    entry["error"] = start_resp
                    results.append(entry)
                    continue

                # Give the service a moment to bind its port
                time.sleep(1.0)

                probe_result = self.probe(host=self.host, port=80, timeout=5)
                entry["probe_result"] = probe_result
                entry["status"] = "verified" if probe_result.get("reachable") else "emulated"

                # Stop the service to free resources
                self.stop_service(service_id)
            except requests.RequestException as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
                logger.warning("Batch emulate error for %s: %s", binary_name, exc)

            results.append(entry)

        logger.info("=== batch_emulate complete: %d/%d verified ===",
                     sum(1 for r in results if r["status"] == "verified"),
                     len(binaries))
        return results

    def quick_validate(
        self,
        firmware_path: str,
        target_binary: str = "httpd",
    ) -> Dict[str, Any]:
        """Quick validation: can this firmware's binary be emulated and
        does it respond?

        A lightweight wrapper around :meth:`emulate_and_probe` that focuses
        on a single binary and returns a concise yes/no answer.

        Args:
            firmware_path: Local path to the firmware file.
            target_binary: Binary to validate against (default ``"httpd"``).

        Returns:
            Dict with keys ``"valid"`` (bool), ``"reason"``, and the full
            pipeline result.
        """
        logger.info("=== quick_validate: %s / %s ===", firmware_path, target_binary)
        result = self.emulate_and_probe(firmware_path, target_binary=target_binary)
        valid = result.get("status") == "verified"
        reason = (
            "Binary emulated and responded to probe"
            if valid
            else f"Status: {result.get('status', 'unknown')}"
        )
        result["valid"] = valid
        result["reason"] = reason
        return result

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()
        logger.debug("HTTP session closed")

    def __enter__(self) -> "EmulationAgentClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
