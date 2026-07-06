"""
Service Probe — network service discovery and health checking.

Probes emulated services to verify they're running correctly.
Supports TCP, HTTP, Telnet, and SSH protocols with auto-detection.
"""

import re
import time
import socket
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Result of probing a network service."""
    reachable: bool
    protocol: str
    host: str
    port: int
    latency_ms: float = 0.0
    banner: Optional[str] = None
    http_status: Optional[int] = None
    http_headers: Optional[Dict[str, str]] = None
    http_body_preview: Optional[str] = None
    telnet_login_prompt: bool = False
    service_name: Optional[str] = None  # e.g., "GoAhead", "dropbear", "dnsmasq"
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "reachable": self.reachable,
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.banner:
            d["banner"] = self.banner
        if self.http_status:
            d["http_status"] = self.http_status
        if self.http_headers:
            d["http_headers"] = self.http_headers
        if self.http_body_preview:
            d["http_body_preview"] = self.http_body_preview
        if self.telnet_login_prompt:
            d["telnet_login_prompt"] = True
        if self.service_name:
            d["service_name"] = self.service_name
        if self.error:
            d["error"] = self.error
        return d


class ProbeManager:
    """Probes network services for reachability and banner information."""

    def __init__(self, default_timeout: float = 5.0):
        self.default_timeout = default_timeout

    def probe(
        self,
        host: str = "127.0.0.1",
        port: int = 80,
        protocol: str = "auto",
        timeout: Optional[float] = None,
        http_path: str = "/",
    ) -> ProbeResult:
        """Probe a service, auto-detecting protocol if not specified.

        Args:
            host: Target host (usually 127.0.0.1 for local QEMU).
            port: Target port.
            protocol: "tcp", "http", "telnet", "ssh", or "auto".
            timeout: Seconds to wait (default: 5).
            http_path: Path for HTTP probe (default: /).

        Returns:
            ProbeResult with reachability and protocol-specific info.
        """
        timeout = timeout or self.default_timeout

        if protocol == "auto":
            return self._probe_auto(host, port, timeout, http_path)
        elif protocol == "http":
            return self.probe_http(host, port, http_path, timeout)
        elif protocol == "telnet":
            return self.probe_telnet(host, port, timeout)
        elif protocol == "ssh":
            return self.probe_ssh(host, port, timeout)
        else:  # raw tcp
            return self.probe_tcp(host, port, timeout)

    # ------------------------------------------------------------------
    # Protocol-specific probes
    # ------------------------------------------------------------------

    def probe_tcp(self, host: str, port: int, timeout: float = 5.0) -> ProbeResult:
        """TCP connect and read initial banner."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            latency = (time.time() - start) * 1000

            # Try to read a banner
            sock.settimeout(1.0)
            banner = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    banner += chunk
                    if len(banner) > 4096:
                        break
            except socket.timeout:
                pass

            sock.close()

            banner_str = banner.decode("utf-8", errors="replace").strip()
            # Truncate for readability
            if len(banner_str) > 500:
                banner_str = banner_str[:500] + "..."

            return ProbeResult(
                reachable=True,
                protocol="tcp",
                host=host,
                port=port,
                latency_ms=latency,
                banner=banner_str or None,
                service_name=self._identify_service(banner_str, port),
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return ProbeResult(
                reachable=False,
                protocol="tcp",
                host=host,
                port=port,
                latency_ms=(time.time() - start) * 1000,
                error=str(e),
            )

    def probe_http(
        self, host: str, port: int, path: str = "/", timeout: float = 5.0
    ) -> ProbeResult:
        """HTTP GET probe."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            latency = (time.time() - start) * 1000

            # Send HTTP request
            request = (
                f"GET {path} HTTP/1.0\r\n"
                f"Host: {host}:{port}\r\n"
                f"User-Agent: EmulationAgent/1.0\r\n"
                f"Accept: */*\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            sock.sendall(request.encode())

            # Read response
            sock.settimeout(timeout)
            response = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    if len(response) > 65536:  # 64KB max
                        break
            except socket.timeout:
                pass

            sock.close()

            response_str = response.decode("utf-8", errors="replace")
            status, headers, body = self._parse_http_response(response_str)

            return ProbeResult(
                reachable=True,
                protocol="http",
                host=host,
                port=port,
                latency_ms=latency,
                http_status=status,
                http_headers=headers,
                http_body_preview=body[:500] if body else None,
                banner=self._extract_server_header(headers),
                service_name=self._identify_http_service(headers, body, port),
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return ProbeResult(
                reachable=False,
                protocol="http",
                host=host,
                port=port,
                latency_ms=(time.time() - start) * 1000,
                error=str(e),
            )

    def probe_telnet(self, host: str, port: int, timeout: float = 5.0) -> ProbeResult:
        """Telnet probe — connect and look for login prompt."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            latency = (time.time() - start) * 1000

            # Read banner and check for login prompt
            sock.settimeout(2.0)
            data = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                    # Telnet negotiation may happen first
                    if b"login:" in data.lower() or b"username:" in data.lower():
                        break
                    if len(data) > 8192:
                        break
            except socket.timeout:
                pass

            sock.close()

            banner_str = data.decode("utf-8", errors="replace").strip()
            if len(banner_str) > 500:
                banner_str = banner_str[:500] + "..."

            # Strip telnet negotiation bytes for cleaner output
            clean_banner = re.sub(rb'\xff[\xfb-\xfe]\x03?', b'', data)
            clean_str = clean_banner.decode("utf-8", errors="replace").strip()

            has_login = bool(
                re.search(r"(login|username)[\s:]*$", clean_str, re.I)
            )

            return ProbeResult(
                reachable=True,
                protocol="telnet",
                host=host,
                port=port,
                latency_ms=latency,
                banner=clean_str[:500] if clean_str else banner_str,
                telnet_login_prompt=has_login,
                service_name="telnetd" if has_login else None,
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return ProbeResult(
                reachable=False,
                protocol="telnet",
                host=host,
                port=port,
                latency_ms=(time.time() - start) * 1000,
                error=str(e),
            )

    def probe_ssh(self, host: str, port: int, timeout: float = 5.0) -> ProbeResult:
        """SSH probe — connect and read SSH banner."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            latency = (time.time() - start) * 1000

            sock.settimeout(2.0)
            banner = sock.recv(256)
            sock.close()

            banner_str = banner.decode("utf-8", errors="replace").strip()
            return ProbeResult(
                reachable=True,
                protocol="ssh",
                host=host,
                port=port,
                latency_ms=latency,
                banner=banner_str,
                service_name=self._identify_ssh_service(banner_str),
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return ProbeResult(
                reachable=False,
                protocol="ssh",
                host=host,
                port=port,
                latency_ms=(time.time() - start) * 1000,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    def _probe_auto(
        self, host: str, port: int, timeout: float, http_path: str
    ) -> ProbeResult:
        """Try protocols in order: HTTP → Telnet → raw TCP."""
        # Try HTTP first (most common for firmware web interfaces)
        http_result = self.probe_http(host, port, http_path, min(timeout, 3.0))
        if http_result.reachable and http_result.http_status:
            return http_result

        # If reachable but not HTTP, try to identify
        if http_result.reachable and http_result.banner:
            banner = http_result.banner.lower()
            if "ssh" in banner:
                return self.probe_ssh(host, port, timeout)
            if any(w in banner for w in ["login", "username", "password"]):
                telnet_result = self.probe_telnet(host, port, timeout)
                if telnet_result.reachable:
                    return telnet_result
            return http_result  # Return the TCP-level result

        # Try raw TCP
        return self.probe_tcp(host, port, timeout)

    # ------------------------------------------------------------------
    # Service identification
    # ------------------------------------------------------------------

    def _parse_http_response(self, response: str) -> tuple:
        """Parse HTTP response into (status_code, headers_dict, body)."""
        status = None
        headers = {}
        body = ""

        lines = response.split("\r\n")
        if not lines:
            return status, headers, body

        # Status line
        status_match = re.match(r"HTTP/\d\.\d\s+(\d{3})", lines[0])
        if status_match:
            status = int(status_match.group(1))

        # Headers
        body_start = 0
        for i, line in enumerate(lines[1:], 1):
            if line == "":
                body_start = i + 1
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        # Body
        body = "\n".join(lines[body_start:])

        return status, headers, body

    def _extract_server_header(self, headers: Dict[str, str]) -> Optional[str]:
        """Extract Server header from HTTP response."""
        return headers.get("server")

    def _identify_service(self, banner: str, port: int) -> Optional[str]:
        """Try to identify service from TCP banner."""
        if not banner:
            return None
        banner_lower = banner.lower()
        if "ssh" in banner_lower and "openssh" in banner_lower:
            return "OpenSSH"
        if "dropbear" in banner_lower:
            return "dropbear"
        if "telnet" in banner_lower:
            return "telnetd"
        if "ftp" in banner_lower:
            return "ftpd"
        if "smtp" in banner_lower:
            return "smtpd"
        if "dnsmasq" in banner_lower:
            return "dnsmasq"
        return None

    def _identify_http_service(
        self, headers: Dict[str, str], body: str, port: int
    ) -> Optional[str]:
        """Identify HTTP server from headers and body content."""
        server = headers.get("server", "")
        if not server:
            # Try to guess from body
            body_lower = body.lower()
            if "goahead" in body_lower:
                return "GoAhead"
            if "mini_httpd" in body_lower:
                return "mini_httpd"
            if "uhttpd" in body_lower:
                return "uHTTPd"
            if "lighttpd" in body_lower:
                return "lighttpd"
            if "boa" in body_lower:
                return "Boa"
            return None

        server_lower = server.lower()
        if "goahead" in server_lower:
            return "GoAhead"
        if "mini_httpd" in server_lower:
            return "mini_httpd"
        if "uhttpd" in server_lower:
            return "uHTTPd"
        if "lighttpd" in server_lower:
            return "lighttpd"
        if "boa" in server_lower:
            return "Boa"
        if "nginx" in server_lower:
            return "nginx"
        if "apache" in server_lower:
            return "Apache"
        if "iis" in server_lower:
            return "IIS"
        return server  # Return raw server string as fallback

    def _identify_ssh_service(self, banner: str) -> Optional[str]:
        """Identify SSH server from banner."""
        if "openssh" in banner.lower():
            match = re.search(r"OpenSSH[_\s](\S+)", banner)
            return f"OpenSSH_{match.group(1)}" if match else "OpenSSH"
        if "dropbear" in banner.lower():
            match = re.search(r"dropbear[_](\S+)", banner)
            return f"dropbear_{match.group(1)}" if match else "dropbear"
        return "SSH"

    # ------------------------------------------------------------------
    # Batch probing
    # ------------------------------------------------------------------

    def probe_ports(
        self,
        host: str = "127.0.0.1",
        ports: Optional[list] = None,
        timeout: float = 2.0,
    ) -> Dict[int, ProbeResult]:
        """Quickly probe multiple ports."""
        if ports is None:
            ports = [21, 22, 23, 25, 53, 80, 443, 8080, 8443, 9000, 9090]

        results = {}
        for port in ports:
            results[port] = self.probe(host, port, "auto", timeout)
        return results

    def wait_for_service(
        self,
        host: str = "127.0.0.1",
        port: int = 80,
        protocol: str = "auto",
        timeout: float = 30.0,
        interval: float = 1.0,
    ) -> ProbeResult:
        """Wait for a service to become reachable.

        Polls until reachable or timeout expires.
        """
        deadline = time.time() + timeout
        last_error = None

        while time.time() < deadline:
            result = self.probe(host, port, protocol, min(interval, 2.0))
            if result.reachable:
                logger.info(
                    f"Service {host}:{port} became reachable after "
                    f"{time.time() - (deadline - timeout):.1f}s"
                )
                return result
            last_error = result.error
            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(min(interval, remaining))

        logger.warning(f"Service {host}:{port} did not become reachable within {timeout}s")
        return ProbeResult(
            reachable=False,
            protocol=protocol,
            host=host,
            port=port,
            error=f"Timeout after {timeout}s (last: {last_error})",
        )
