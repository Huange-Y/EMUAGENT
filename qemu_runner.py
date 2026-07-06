"""
QEMU Runner — process lifecycle management for emulated firmware services.

Manages QEMU user-mode and system-mode processes: start, stop, monitor,
log capture, port allocation, crash detection.
"""

import os
import re
import sys
import json
import time
import signal
import shutil
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ServiceInfo:
    """Information about a running emulated service."""
    service_id: str
    rootfs_id: str
    binary_path: str
    binary_name: str
    arch: str
    pid: int
    port: Optional[int]
    command: List[str]
    env: Dict[str, str]
    status: str          # "running", "crashed", "exited", "timeout"
    started_at: str
    logs_dir: str
    exit_code: Optional[int] = None
    exit_reason: Optional[str] = None


class QemuRunner:
    """Manages QEMU process lifecycle for firmware service emulation."""

    def __init__(
        self,
        qemu_binaries: Optional[Dict[str, Dict[str, str]]] = None,
        logs_base_dir: str = "/tmp/emulation_agent/logs",
        default_timeout: int = 30,
    ):
        self.qemu_binaries: Dict[str, Dict[str, str]] = qemu_binaries or {}
        self.logs_base_dir = os.path.abspath(logs_base_dir)
        os.makedirs(self.logs_base_dir, exist_ok=True)
        self.default_timeout = default_timeout

        # Runtime state
        self.services: Dict[str, ServiceInfo] = {}
        self._service_counter = 0
        self._port_allocator = _PortAllocator(9000, 9999)

    # ------------------------------------------------------------------
    # User-mode emulation
    # ------------------------------------------------------------------

    def start_user_mode(
        self,
        rootfs_path: str,
        binary_path: str,
        arch: str,
        rootfs_id: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        port: Optional[int] = None,
        timeout: Optional[int] = None,
        strace: bool = False,
        extra_qemu_args: Optional[List[str]] = None,
    ) -> ServiceInfo:
        """Start a binary using QEMU user-mode emulation.

        Args:
            rootfs_path: Path to extracted rootfs.
            binary_path: Path to the binary relative to rootfs.
            arch: Target architecture (mipsel, arm, etc.).
            rootfs_id: Rootfs ID for tracking.
            args: Arguments to pass to the binary.
            env: Environment variables.
            port: TCP port for the service (auto-allocated if None).
            timeout: Seconds before force-kill (uses default if None).
            strace: Enable QEMU strace logging.
            extra_qemu_args: Additional QEMU arguments.

        Returns:
            ServiceInfo with service_id, pid, port, status.
        """
        qemu_binary = self._resolve_qemu_user(arch)
        if not qemu_binary:
            raise RuntimeError(
                f"No QEMU user-mode binary found for arch '{arch}'. "
                f"Available: {list(self.qemu_binaries.keys())}"
            )

        # Absolute paths
        abs_rootfs = os.path.abspath(rootfs_path)
        abs_binary = os.path.join(abs_rootfs, binary_path.lstrip("/"))
        if not os.path.isfile(abs_binary):
            raise FileNotFoundError(f"Binary not found: {abs_binary}")

        service_id = self._next_service_id()
        binary_name = os.path.basename(binary_path)
        timeout = timeout or self.default_timeout
        env = env or {}
        args = args or []

        # Allocate port if service looks network-facing
        allocated_port = None
        if self._is_network_binary(binary_name):
            allocated_port = port or self._port_allocator.allocate()
        elif port:
            allocated_port = port

        # Build QEMU command
        cmd = [qemu_binary]
        if strace:
            cmd.extend(["-strace", "-d", "in_asm,cpu"])
        cmd.extend(["-L", abs_rootfs])

        # Add environment variables
        for key, value in env.items():
            cmd.extend(["-E", f"{key}={value}"])

        # Extra QEMU args
        if extra_qemu_args:
            cmd.extend(extra_qemu_args)

        # The binary and its args
        cmd.append(abs_binary)
        cmd.extend(args)

        # Set up log directory
        logs_dir = os.path.join(self.logs_base_dir, service_id)
        os.makedirs(logs_dir, exist_ok=True)
        stdout_log = os.path.join(logs_dir, "stdout.log")
        stderr_log = os.path.join(logs_dir, "stderr.log")

        logger.info(f"Starting QEMU user-mode: {' '.join(cmd)}")

        # Start the process
        try:
            with open(stdout_log, "w") as out_f, open(stderr_log, "w") as err_f:
                process = subprocess.Popen(
                    cmd,
                    stdout=out_f,
                    stderr=err_f,
                    cwd=abs_rootfs,
                    preexec_fn=os.setsid,  # Create process group for clean kill
                    env={"PATH": "/usr/bin:/bin"},  # Minimal parent env
                )
        except Exception as e:
            logger.error(f"Failed to start QEMU: {e}")
            if allocated_port:
                self._port_allocator.release(allocated_port)
            raise

        # Wait briefly to check if it crashes immediately
        time.sleep(0.5)
        status = "running"
        exit_code = None
        exit_reason = None

        if process.poll() is not None:
            status = "crashed"
            exit_code = process.returncode
            exit_reason = self._analyze_crash(stdout_log, stderr_log)
            logger.warning(
                f"Service {service_id} ({binary_name}) crashed immediately: "
                f"exit={exit_code}, reason={exit_reason}"
            )

        info = ServiceInfo(
            service_id=service_id,
            rootfs_id=rootfs_id,
            binary_path=binary_path,
            binary_name=binary_name,
            arch=arch,
            pid=process.pid,
            port=allocated_port,
            command=cmd,
            env=env,
            status=status,
            started_at=datetime.now().isoformat(),
            logs_dir=logs_dir,
            exit_code=exit_code,
            exit_reason=exit_reason,
        )

        self.services[service_id] = info
        return info

    # ------------------------------------------------------------------
    # System-mode emulation
    # ------------------------------------------------------------------

    def start_system_mode(
        self,
        kernel_path: str,
        rootfs_img: str,
        arch: str,
        qemu_args: Optional[List[str]] = None,
        machine: Optional[str] = None,
        memory: str = "256M",
        port_forwards: Optional[Dict[int, int]] = None,  # host:guest
    ) -> ServiceInfo:
        """Start full system emulation with QEMU system-mode.

        Args:
            kernel_path: Path to kernel image (vmlinux, zImage, etc.).
            rootfs_img: Path to rootfs image (ext2, squashfs, initrd, etc.).
            arch: Target architecture.
            qemu_args: Additional QEMU arguments.
            machine: QEMU machine type (auto-detected if None).
            memory: RAM size.
            port_forwards: Mapping of host_port -> guest_port for -net user,hostfwd=...

        Returns:
            ServiceInfo for the system-mode QEMU process.
        """
        qemu_binary = self._resolve_qemu_system(arch)
        if not qemu_binary:
            raise RuntimeError(
                f"No QEMU system binary found for arch '{arch}'"
            )

        service_id = self._next_service_id()

        cmd = [qemu_binary]
        if machine:
            cmd.extend(["-M", machine])
        cmd.extend(["-m", memory])

        # Kernel
        cmd.extend(["-kernel", kernel_path])

        # Rootfs - detect format
        rootfs_ext = os.path.splitext(rootfs_img)[1]
        if rootfs_img.endswith(".cpio.gz") or rootfs_img.endswith(".initrd"):
            cmd.extend(["-initrd", rootfs_img])
        elif ".squashfs" in rootfs_img or rootfs_img.endswith(".ext2") or rootfs_img.endswith(".ext4"):
            cmd.extend(["-drive", f"file={rootfs_img},format=raw,if=virtio"])
        else:
            cmd.extend(["-drive", f"file={rootfs_img},format=raw"])

        # Append root= kernel param
        cmd.extend(["-append", "console=ttyS0 root=/dev/vda rw init=/sbin/init"])

        # Network
        net_args = ["-net", "nic"]
        if port_forwards:
            hostfwd = ",".join(
                f"hostfwd=tcp::{host}-:{guest}"
                for host, guest in port_forwards.items()
            )
            net_args = ["-net", f"user,{hostfwd}", "-net", "nic"]
        else:
            net_args = ["-net", "user", "-net", "nic"]
        cmd.extend(net_args)

        # Display
        cmd.append("-nographic")

        # Extra args
        if qemu_args:
            cmd.extend(qemu_args)

        logs_dir = os.path.join(self.logs_base_dir, service_id)
        os.makedirs(logs_dir, exist_ok=True)
        stdout_log = os.path.join(logs_dir, "stdout.log")
        stderr_log = os.path.join(logs_dir, "stderr.log")

        logger.info(f"Starting QEMU system-mode: {' '.join(cmd)}")

        with open(stdout_log, "w") as out_f, open(stderr_log, "w") as err_f:
            process = subprocess.Popen(
                cmd,
                stdout=out_f,
                stderr=err_f,
                preexec_fn=os.setsid,
            )

        time.sleep(1)
        status = "running" if process.poll() is None else "crashed"

        info = ServiceInfo(
            service_id=service_id,
            rootfs_id="system_emu",
            binary_path=kernel_path,
            binary_name=os.path.basename(kernel_path),
            arch=arch,
            pid=process.pid,
            port=list(port_forwards.keys())[0] if port_forwards else None,
            command=cmd,
            env={},
            status=status,
            started_at=datetime.now().isoformat(),
            logs_dir=logs_dir,
            exit_code=process.returncode if process.poll() else None,
        )

        self.services[service_id] = info
        return info

    # ------------------------------------------------------------------
    # Service management
    # ------------------------------------------------------------------

    def stop_service(self, service_id: str, force: bool = False) -> bool:
        """Stop a running emulated service.

        Args:
            service_id: Service to stop.
            force: If True, SIGKILL immediately. Otherwise SIGTERM then SIGKILL after 5s.

        Returns:
            True if service was stopped.
        """
        info = self.services.get(service_id)
        if not info:
            logger.warning(f"Service {service_id} not found")
            return False

        if not self.is_service_alive(service_id):
            logger.info(f"Service {service_id} already dead")
            self._release_service_resources(info)
            info.status = "exited"
            return True

        try:
            if force:
                os.killpg(os.getpgid(info.pid), signal.SIGKILL)
            else:
                os.killpg(os.getpgid(info.pid), signal.SIGTERM)
                # Wait up to 5s for graceful shutdown
                for _ in range(10):
                    time.sleep(0.5)
                    if not self.is_service_alive(service_id):
                        break
                else:
                    # Force kill
                    if self.is_service_alive(service_id):
                        os.killpg(os.getpgid(info.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass  # Already dead

        info.status = "exited"
        self._release_service_resources(info)
        logger.info(f"Stopped service {service_id} ({info.binary_name})")
        return True

    def is_service_alive(self, service_id: str) -> bool:
        """Check if a service process is still running."""
        info = self.services.get(service_id)
        if not info:
            return False
        try:
            os.kill(info.pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False

    def get_service(self, service_id: str) -> Optional[ServiceInfo]:
        """Get service info."""
        return self.services.get(service_id)

    def list_services(self, rootfs_id: Optional[str] = None) -> List[ServiceInfo]:
        """List services, optionally filtered by rootfs_id."""
        services = []
        for info in self.services.values():
            # Update status for stale entries
            if info.status == "running" and not self.is_service_alive(info.service_id):
                info.status = "exited"
            if rootfs_id and info.rootfs_id != rootfs_id:
                continue
            services.append(info)
        return services

    def get_service_logs(
        self, service_id: str, tail: int = 100
    ) -> Dict[str, str]:
        """Get recent logs from a service."""
        info = self.services.get(service_id)
        if not info:
            return {"stdout": "", "stderr": ""}

        result = {}
        for log_type in ["stdout", "stderr"]:
            log_path = os.path.join(info.logs_dir, f"{log_type}.log")
            if os.path.isfile(log_path):
                try:
                    with open(log_path, "r", errors="replace") as f:
                        lines = f.readlines()
                        result[log_type] = "".join(lines[-tail:])
                except Exception as e:
                    result[log_type] = f"[Error reading log: {e}]"
            else:
                result[log_type] = ""
        return result

    def stop_all_services(self, rootfs_id: Optional[str] = None):
        """Stop all services, optionally filtered by rootfs_id."""
        for sid in list(self.services.keys()):
            info = self.services[sid]
            if rootfs_id and info.rootfs_id != rootfs_id:
                continue
            self.stop_service(sid, force=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_service_id(self) -> str:
        self._service_counter += 1
        return f"srv_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self._service_counter:04d}"

    def _resolve_qemu_user(self, arch: str) -> Optional[str]:
        """Find qemu-user-static binary for an architecture."""
        # Direct match
        if arch in self.qemu_binaries:
            user_bin = self.qemu_binaries[arch].get("user")
            if user_bin:
                return user_bin

        # Fallback: search PATH
        for search_name in [f"qemu-{arch}-static", f"qemu-{arch}"]:
            found = shutil.which(search_name)
            if found:
                return found

        return None

    def _resolve_qemu_system(self, arch: str) -> Optional[str]:
        """Find qemu-system binary for an architecture."""
        if arch in self.qemu_binaries:
            sys_bin = self.qemu_binaries[arch].get("system")
            if sys_bin:
                return sys_bin

        for search_name in [f"qemu-system-{arch}"]:
            found = shutil.which(search_name)
            if found:
                return found

        return None

    def _is_network_binary(self, binary_name: str) -> bool:
        """Heuristic: does this binary likely listen on a network port?"""
        network_names = [
            "httpd", "nginx", "lighttpd", "apache2",
            "telnetd", "telnet", "sshd", "dropbear",
            "dnsmasq", "dns", "named",
            "upnpd", "miniupnpd",
            "snmpd", "snmp",
            "ftpd", "vsftpd", "proftpd",
            "smbd", "nmbd",
            "rtspd", "rtsp",
            "mqtt", "mosquitto",
            "lldpd", "lldp",
            "cwmp", "tr069",
            "goahead", "boa", "mini_httpd", "thttpd",
            "uhttpd", "microhttpd",
            "netcat", "nc",
        ]
        name_lower = binary_name.lower()
        return any(n in name_lower for n in network_names)

    def _release_service_resources(self, info: ServiceInfo):
        """Release port and other resources held by a service."""
        if info.port:
            self._port_allocator.release(info.port)

    def _analyze_crash(self, stdout_log: str, stderr_log: str) -> str:
        """Analyze log files to determine crash reason."""
        reasons = []
        for log_path in [stdout_log, stderr_log]:
            if not os.path.isfile(log_path):
                continue
            try:
                with open(log_path, "r", errors="replace") as f:
                    content = f.read()
                if "Segmentation fault" in content:
                    reasons.append("SIGSEGV")
                if "Illegal instruction" in content:
                    reasons.append("SIGILL")
                if "Bus error" in content:
                    reasons.append("SIGBUS")
                if "Aborted" in content or "ABORT" in content:
                    reasons.append("SIGABRT")
                if "cannot open shared object file" in content.lower():
                    match = re.search(r"cannot open shared object file[:\s]*(.+)", content, re.I)
                    if match:
                        reasons.append(f"missing_lib:{match.group(1).strip()}")
                if "No such file or directory" in content:
                    reasons.append("file_not_found")
                if "/dev/mem" in content.lower() or "mmap" in content.lower():
                    reasons.append("hardware_access")
            except Exception:
                pass

        return "; ".join(reasons) if reasons else "unknown"

    def check_services_health(self) -> Dict[str, str]:
        """Check health of all running services. Updates status in place."""
        updates = {}
        for sid, info in list(self.services.items()):
            if info.status == "running":
                if not self.is_service_alive(sid):
                    info.status = "exited"
                    updates[sid] = "exited"
                else:
                    updates[sid] = "running"
        return updates


class _PortAllocator:
    """Thread-safe-ish port allocator for the configured range."""

    def __init__(self, start: int = 9000, end: int = 9999):
        self.start = start
        self.end = end
        self.used: set = set()

    def allocate(self) -> int:
        """Find and reserve a free port."""
        import socket
        for port in range(self.start, self.end + 1):
            if port in self.used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", port))
                    self.used.add(port)
                    return port
                except OSError:
                    continue
        raise RuntimeError(f"No free ports in range {self.start}-{self.end}")

    def release(self, port: int):
        """Release a port back to the pool."""
        self.used.discard(port)
