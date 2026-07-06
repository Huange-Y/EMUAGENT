"""
Command-line interface for the Emulation Agent.

Provides ``emu-server`` and ``emu`` command groups for managing the agent
server and interacting with emulated firmware services.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import click

# Dual import: works both as installed package and direct script
try:
    from emulation_agent.client import EmulationAgentClient
    from emulation_agent import __version__
except ModuleNotFoundError:
    from client import EmulationAgentClient
    from __init__ import __version__

# ---------------------------------------------------------------------------
# Output helpers (colour + progress)
# ---------------------------------------------------------------------------

# Rich library is optional — fall back to plain click.style if not installed.
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich import print as rprint

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


class Output:
    """Unified output abstraction that delegates to Rich when available."""

    def __init__(self) -> None:
        if _RICH_AVAILABLE:
            self._console = Console()
        else:
            self._console = None

    def print(self, *args: Any, **kwargs: Any) -> None:
        if _RICH_AVAILABLE:
            rprint(*args)
        else:
            click.echo(" ".join(str(a) for a in args))

    def success(self, message: str) -> None:
        if _RICH_AVAILABLE:
            rprint(f"[bold green]✓[/bold green] {message}")
        else:
            click.secho(f"✓ {message}", fg="green", bold=True)

    def error(self, message: str) -> None:
        if _RICH_AVAILABLE:
            rprint(f"[bold red]✗[/bold red] {message}")
        else:
            click.secho(f"✗ {message}", fg="red", bold=True, err=True)

    def warning(self, message: str) -> None:
        if _RICH_AVAILABLE:
            rprint(f"[bold yellow]⚠[/bold yellow] {message}")
        else:
            click.secho(f"⚠ {message}", fg="yellow", bold=True)

    def info(self, message: str) -> None:
        if _RICH_AVAILABLE:
            rprint(f"[bold cyan]ℹ[/bold cyan] {message}")
        else:
            click.secho(f"ℹ {message}", fg="cyan", bold=True)

    def echo(self, message: str = "", **kwargs: Any) -> None:
        """Print plain text (passthrough to click.echo)."""
        click.echo(message, **kwargs)

    def bold(self, message: str) -> str:
        """Return bold-styled string (for embedding in other output)."""
        if _RICH_AVAILABLE:
            return f"[bold]{message}[/bold]"
        return click.style(message, bold=True)

    def json_output(self, data: Dict[str, Any]) -> None:
        """Pretty-print a dict as JSON."""
        self.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def table(self, title: str, columns: List[str], rows: List[List[Any]]) -> None:
        """Render a formatted table."""
        if _RICH_AVAILABLE:
            table = Table(title=title)
            for col in columns:
                table.add_column(col, style="cyan")
            for row in rows:
                table.add_row(*[str(c) for c in row])
            self._console.print(table)  # type: ignore[union-attr]
        else:
            # Simple column-aligned output
            click.echo(f"\n{title}")
            click.echo("-" * len(title))
            if rows:
                col_widths = [
                    max(len(str(row[i])) for row in rows + [columns])
                    for i in range(len(columns))
                ]
                header = "  ".join(c.ljust(w) for c, w in zip(columns, col_widths))
                click.secho(header, bold=True)
                for row in rows:
                    line = "  ".join(str(r).ljust(w) for r, w in zip(row, col_widths))
                    click.echo(line)
            click.echo()

    def panel(self, content: str, title: str = "") -> None:
        """Display content inside a bordered panel."""
        if _RICH_AVAILABLE:
            self._console.print(Panel(content, title=title))  # type: ignore[union-attr]
        else:
            if title:
                click.secho(f"--- {title} ---", bold=True)
            click.echo(content)
            if title:
                click.secho("-" * (len(title) + 8), bold=True)

    def spinner(self, message: str) -> Any:
        """Return a context manager that shows a spinner while work is done.

        Returns:
            A *Progress* instance (Rich) or a dummy context manager.
        """
        if _RICH_AVAILABLE:
            return Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            )
        else:
            return _DummySpinner(message)

    def progress(self, message: str) -> Any:
        """Alias for spinner — shows progress indicator during long operations."""
        return self.spinner(message)


class _DummySpinner:
    """No-op spinner for when Rich is not available."""

    def __init__(self, message: str) -> None:
        self._message = message

    def __enter__(self) -> "_DummySpinner":
        click.echo(f"{self._message}...", nl=False)
        return self

    def __exit__(self, *args: Any) -> None:
        click.echo(" done.")

    def add_task(self, description: str, total: Optional[int] = None) -> "_DummyTask":
        return _DummyTask()

    def start_task(self, task_id: Any) -> None:
        pass

    def stop_task(self, task_id: Any) -> None:
        pass

    def update(self, task_id: Any, advance: float = 0) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _DummyTask:
    pass


out = Output()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_client() -> EmulationAgentClient:
    """Build a client from environment / defaults."""
    host = os.environ.get("EMU_AGENT_HOST", "your-vm-ip")
    port = int(os.environ.get("EMU_AGENT_PORT", "9100"))
    timeout = int(os.environ.get("EMU_AGENT_TIMEOUT", "30"))
    return EmulationAgentClient(host=host, port=port, timeout=timeout)


def _print_error_and_exit(exc: Exception, context: str = "") -> None:
    """Log an error and exit with code 1."""
    msg = f"{context}: {exc}" if context else str(exc)
    out.error(msg)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# emu-server commands
# ---------------------------------------------------------------------------


@click.group(name="emu-server")
def emu_server_group() -> None:
    """Manage the Emulation Agent server process."""


@emu_server_group.command("start")
@click.option("--host", default="0.0.0.0", help="Bind address.")
@click.option("--port", default=9100, help="Listen port.")
@click.option("--log-level", default="info", help="Uvicorn log level.")
@click.option("--reload", is_flag=True, help="Enable auto-reload (dev only).")
def server_start(host: str, port: int, log_level: str, reload: bool) -> None:
    """Start the emulation agent server (foreground)."""
    out.info(f"Starting emulation agent server on {host}:{port}")
    try:
        import uvicorn
        try:
            from emulation_agent.server import create_app
        except ModuleNotFoundError:
            from server import create_app

        app = create_app()
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level,
            reload=reload,
        )
    except ImportError as exc:
        _print_error_and_exit(exc, "Missing dependency. Install with: pip install uvicorn")
    except KeyboardInterrupt:
        out.warning("Server stopped by user")
    except Exception as exc:
        _print_error_and_exit(exc, "Failed to start server")


@emu_server_group.command("stop")
def server_stop() -> None:
    """Stop a running emulation agent server."""
    out.info("Stopping emulation agent server...")
    try:
        import psutil

        killed = False
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "uvicorn" in cmdline and "server:app" in cmdline:
                    out.info(f"Killing process {proc.info['pid']} ({cmdline[:80]})")
                    proc.terminate()
                    proc.wait(timeout=5)
                    killed = True
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass

        if killed:
            out.success("Server stopped")
        else:
            out.warning("No running emulation agent server found")
    except ImportError:
        # Fallback: use pkill
        try:
            result = subprocess.run(
                ["pkill", "-f", "uvicorn.*server:app"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                out.success("Server stopped (via pkill)")
            else:
                out.warning("No running emulation agent server found")
        except FileNotFoundError:
            out.error("Neither psutil nor pkill available; cannot stop server")


@emu_server_group.command("status")
def server_status() -> None:
    """Check if the emulation agent server is reachable."""
    client = _get_client()
    try:
        with out.spinner("Checking server status") as spinner:
            task = spinner.add_task("Connecting...", total=None)
            spinner.start()
            result = client.health()
            spinner.update(task, advance=1)
        status = result.get("status", "unknown")
        if status == "ok":
            out.success(f"Server is healthy at {client.base_url}")
            out.json_output(result)
        else:
            out.warning(f"Server responded but status={status}")
            out.json_output(result)
    except Exception as exc:
        out.error(f"Server not reachable at {client.base_url}: {exc}")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# emu commands
# ---------------------------------------------------------------------------


@click.group(name="emu")
def emu_group() -> None:
    """Interact with a remote Emulation Agent server."""


@emu_group.command("upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "fmt", default="auto", help="Firmware format hint.")
def emu_upload(file: str, fmt: str) -> None:
    """Upload firmware or rootfs to the agent."""
    client = _get_client()
    try:
        with out.spinner(f"Uploading {file}") as spinner:
            task = spinner.add_task("Uploading...", total=None)
            spinner.start()
            result = client.upload_rootfs(file_path=file, format=fmt)
            spinner.update(task, advance=1)
        rootfs_id = result.get("rootfs_id")
        if rootfs_id:
            out.success(f"Uploaded! rootfs_id = {rootfs_id}")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "Upload failed")


@emu_group.command("list")
@click.argument("rootfs_id", required=False)
def emu_list(rootfs_id: Optional[str]) -> None:
    """List rootfs entries or binaries inside a rootfs."""
    client = _get_client()
    try:
        if rootfs_id:
            # List files / binaries inside the rootfs via exec
            result = client.exec_command(rootfs_id, "find /bin /sbin /usr/bin /usr/sbin -type f 2>/dev/null | head -50 || ls -la /")
            out.panel(result.get("stdout", ""), title=f"Binaries in {rootfs_id}")
        else:
            result = client.list_services()
            services = result.get("services", [])
            if not services:
                out.info("No running services. Use 'emu upload' to upload a rootfs first.")
            else:
                rows = []
                for svc in services:
                    rows.append([
                        svc.get("service_id", "?"),
                        svc.get("binary_name", "?"),
                        svc.get("status", "?"),
                        svc.get("port", "?"),
                    ])
                out.table("Running Services", ["Service ID", "Binary", "Status", "Port"], rows)
    except Exception as exc:
        _print_error_and_exit(exc, "List failed")


@emu_group.command("start")
@click.argument("rootfs_id")
@click.argument("binary")
@click.option("--args", "-a", multiple=True, help="Arguments for the binary.")
@click.option("--port", "-p", type=int, default=None, help="Expected listening port.")
@click.option("--timeout", "-t", type=int, default=30, help="Start timeout (seconds).")
def emu_start(
    rootfs_id: str,
    binary: str,
    args: Tuple[str, ...],
    port: Optional[int],
    timeout: int,
) -> None:
    """Start emulating a binary from a rootfs."""
    client = _get_client()
    try:
        with out.spinner(f"Starting {binary} in {rootfs_id}") as spinner:
            task = spinner.add_task("Starting...", total=None)
            spinner.start()
            result = client.start_service(
                rootfs_id=rootfs_id,
                binary_name=binary,
                args=list(args) if args else None,
                port=port,
                timeout=timeout,
            )
            spinner.update(task, advance=1)
        service_id = result.get("service_id")
        status = result.get("status", "unknown")
        if status in ("running", "started"):
            out.success(f"Started {binary} — service_id = {service_id}")
        else:
            out.warning(f"Service started with status={status}")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "Start failed")


@emu_group.command("stop")
@click.argument("service_id")
def emu_stop(service_id: str) -> None:
    """Stop an emulated service."""
    client = _get_client()
    try:
        result = client.stop_service(service_id)
        out.success(f"Stopped service {service_id}")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "Stop failed")


@emu_group.command("probe")
@click.argument("host")
@click.argument("port", type=int)
@click.option("--protocol", "-P", default="auto", help="Protocol: auto, tcp, http, telnet.")
@click.option("--timeout", "-t", type=int, default=5, help="Probe timeout.")
def emu_probe(host: str, port: int, protocol: str, timeout: int) -> None:
    """Probe a service on host:port."""
    client = _get_client()
    try:
        with out.spinner(f"Probing {host}:{port}") as spinner:
            task = spinner.add_task("Probing...", total=None)
            spinner.start()
            result = client.probe(host=host, port=port, protocol=protocol, timeout=timeout)
            spinner.update(task, advance=1)
        reachable = result.get("reachable", False)
        if reachable:
            out.success(f"{host}:{port} is REACHABLE ({result.get('protocol', '?')})")
        else:
            out.warning(f"{host}:{port} is NOT reachable")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "Probe failed")


@emu_group.command("exec")
@click.argument("rootfs_id")
@click.argument("cmd")
@click.option("--timeout", "-t", type=int, default=10, help="Command timeout (seconds).")
def emu_exec(rootfs_id: str, cmd: str, timeout: int) -> None:
    """Execute a command in an emulated rootfs environment."""
    client = _get_client()
    try:
        with out.spinner(f"Executing in {rootfs_id}: {cmd[:60]}") as spinner:
            task = spinner.add_task("Running...", total=None)
            spinner.start()
            result = client.exec_command(rootfs_id=rootfs_id, command=cmd, timeout=timeout)
            spinner.update(task, advance=1)
        rc = result.get("returncode", 1)
        if rc == 0:
            out.success("Command completed successfully")
        else:
            out.warning(f"Command exited with returncode={rc}")
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stdout:
            out.panel(stdout.strip(), title="stdout")
        if stderr:
            out.panel(stderr.strip(), title="stderr")
    except Exception as exc:
        _print_error_and_exit(exc, "Exec failed")


@emu_group.command("logs")
@click.argument("service_id")
@click.option("--tail", "-n", type=int, default=100, help="Lines to tail.")
def emu_logs(service_id: str, tail: int) -> None:
    """Get logs for a specific emulated service."""
    client = _get_client()
    try:
        result = client.get_service_logs(service_id, tail=tail)
        logs = result.get("logs", "")
        out.panel(logs.strip() or "(empty)", title=f"Logs for {service_id}")
    except Exception as exc:
        _print_error_and_exit(exc, "Logs fetch failed")


@emu_group.command("ps")
def emu_ps() -> None:
    """List running emulated services."""
    client = _get_client()
    try:
        result = client.list_services()
        services = result.get("services", [])
        if not services:
            out.info("No services running.")
            return
        rows = []
        for svc in services:
            rows.append([
                svc.get("service_id", "?"),
                svc.get("binary_name", svc.get("binary_path", "?")),
                svc.get("status", "?"),
                str(svc.get("port", "?")),
                svc.get("pid", "?"),
            ])
        out.table("Running Services", ["Service ID", "Binary", "Status", "Port", "PID"], rows)
    except Exception as exc:
        _print_error_and_exit(exc, "List failed")


@emu_group.command("nvram")
@click.argument("rootfs_id")
@click.argument("device_type", required=False, default="auto")
@click.option("--config", "-c", "config_json", default=None, help="JSON string of NVRAM key/values.")
def emu_nvram(rootfs_id: str, device_type: str, config_json: Optional[str]) -> None:
    """Configure NVRAM for a rootfs (for GoAhead/Tenda-style web servers)."""
    client = _get_client()
    config_dict: Optional[Dict[str, str]] = None
    if config_json:
        try:
            config_dict = json.loads(config_json)
        except json.JSONDecodeError as exc:
            out.error(f"Invalid JSON config: {exc}")
            raise SystemExit(1)
    try:
        result = client.configure_nvram(
            rootfs_id=rootfs_id,
            device_type=device_type,
            config=config_dict,
        )
        out.success(f"NVRAM configured for {rootfs_id} (device_type={device_type})")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "NVRAM config failed")


@emu_group.command("quick")
@click.argument("firmware", type=click.Path(exists=True, dir_okay=False))
@click.argument("binary", required=False, default="httpd")
def emu_quick(firmware: str, binary: str) -> None:
    """Quick emulate-and-probe pipeline for a firmware file."""
    client = _get_client()
    try:
        with out.spinner(f"Quick validation: {firmware} / {binary}") as spinner:
            task = spinner.add_task("Pipeline running...", total=None)
            spinner.start()
            result = client.quick_validate(firmware_path=firmware, target_binary=binary)
            spinner.update(task, advance=1)

        valid = result.get("valid", False)
        if valid:
            out.success(f"[VALID] {binary} emulated and responsive!")
        else:
            out.error(f"[NOT VALID] {result.get('reason', 'unknown')}")
        out.json_output(result)
    except Exception as exc:
        _print_error_and_exit(exc, "Quick validation failed")


@emu_group.command("deploy")
@click.option("--host", default="your-vm-ip", help="Remote host.")
@click.option("--port", default=22, help="SSH port.")
@click.option("--user", default="art", help="SSH user.")
@click.option("--key", default=None, help="SSH private key path.")
@click.option("--update-only", is_flag=True, help="Only update code, skip deps.")
@click.option("--restart", is_flag=True, help="Restart existing server.")
@click.option("--logs", "show_logs", is_flag=True, help="Tail remote server logs.")
@click.option("--status", "show_status", is_flag=True, help="Check remote server status.")
def emu_deploy(
    host: str,
    port: int,
    user: str,
    key: Optional[str],
    update_only: bool,
    restart: bool,
    show_logs: bool,
    show_status: bool,
) -> None:
    """Deploy the emulation agent to a remote Ubuntu VM."""
    # Locate deploy script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    deploy_sh = os.path.join(script_dir, "deploy.sh")
    if not os.path.isfile(deploy_sh):
        out.error(f"deploy.sh not found at {deploy_sh}")
        raise SystemExit(1)

    cmd = ["bash", deploy_sh, host, str(port), user]
    env_vars = os.environ.copy()
    if key:
        env_vars["SSH_KEY"] = key

    extra_args = []
    if update_only:
        extra_args.append("--update")
    if restart:
        extra_args.append("--restart")
    if show_logs:
        extra_args.append("--logs")
    if show_status:
        extra_args.append("--status")

    full_cmd = cmd + extra_args
    out.info(f"Running: {' '.join(full_cmd)}")
    try:
        result = subprocess.run(
            full_cmd,
            env=env_vars,
            text=True,
        )
        if result.returncode == 0:
            out.success("Deploy completed")
        else:
            out.error(f"Deploy exited with code {result.returncode}")
            raise SystemExit(result.returncode)
    except FileNotFoundError:
        _print_error_and_exit(Exception("bash not found"), "Cannot run deploy script")
    except KeyboardInterrupt:
        out.warning("Deploy cancelled by user")


# ---------------------------------------------------------------------------
# CLI entry point (combined group)
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="emulation-agent")
def cli() -> None:
    """Emulation Agent — Firmware emulation for vulnerability research.

    Manage the emulation agent server and interact with emulated firmware
    services from the command line.
    """
    pass


cli.add_command(emu_server_group)
cli.add_command(emu_group)


# ---------------------------------------------------------------------------
# emu fetch — firmware acquisition commands
# ---------------------------------------------------------------------------

@click.group("fetch", help="Search and download firmware images.")
def fetch_group() -> None:
    """Search and download firmware from vendor sites and open-source projects."""
    pass


@fetch_group.command("search")
@click.argument("query")
@click.option("--vendor", "-v", default=None, help="Limit to specific vendor.")
@click.option("--max-results", "-n", default=10, help="Maximum results.")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
def fetch_search(query: str, vendor: Optional[str], max_results: int, output_json: bool) -> None:
    """Search for firmware images by vendor, model, or CVE."""
    from firmware_acquisition import FirmwareAcquisition

    fa = FirmwareAcquisition()
    results = fa.search(query, vendor=vendor, max_results=max_results, use_cache=True)

    if output_json:
        click.echo(json.dumps([r.to_dict() for r in results], indent=2))
        return

    if not results:
        out.warning(f"No firmware found for: {query}")
        return

    out.success(f"Found {len(results)} firmware images for '{query}':")
    for i, r in enumerate(results):
        out.echo(f"\n  [{i+1}] {out.bold(r.display_name)}")
        out.echo(f"      Vendor:  {r.vendor}")
        out.echo(f"      Source:  {r.source} (reliability: {r.reliability:.0%})")
        if r.version:
            out.echo(f"      Version: {r.version}")
        if r.arch_hint:
            out.echo(f"      Arch:    {r.arch_hint}")
        if r.cve_ids:
            out.echo(f"      CVEs:    {', '.join(r.cve_ids)}")
        out.echo(f"      URL:     {r.url[:120]}")
        if r.size_bytes:
            out.echo(f"      Size:    {r.size_bytes / 1024 / 1024:.1f} MB")


@fetch_group.command("download")
@click.argument("url")
@click.option("--output-dir", "-o", default=None, help="Output directory.")
@click.option("--no-verify", is_flag=True, help="Skip checksum verification.")
@click.option("--emulate", is_flag=True, help="Feed into emulation agent after download.")
@click.option("--agent-url", default="http://127.0.0.1:9100", help="Emulation agent URL.")
def fetch_download(
    url: str, output_dir: Optional[str], no_verify: bool, emulate: bool, agent_url: str
) -> None:
    """Download firmware from a URL."""
    from firmware_acquisition import FirmwareAcquisition, FirmwareEntry

    fa = FirmwareAcquisition()
    entry = FirmwareEntry(
        vendor="manual", model="manual", version="",
        url=url, source="manual", reliability=1.0,
    )

    with out.progress(f"Downloading from {url[:80]}...") as task:
        path = fa.download(entry, output_dir=output_dir, verify=not no_verify)

    if not path:
        out.error("Download failed")
        raise SystemExit(1)

    out.success(f"Downloaded: {path}")
    size_mb = os.path.getsize(path) / 1024 / 1024
    out.echo(f"Size: {size_mb:.1f} MB")

    if emulate:
        out.echo(f"\nStarting emulation via {agent_url}...")
        try:
            result = EmulationAgentClient(
                host=agent_url.replace("http://", "").split(":")[0],
                port=int(agent_url.split(":")[-1]) if ":" in agent_url.split("//")[-1] else 9100,
            ).emulate_and_probe(path)
            probe = result.get("steps", {}).get("probe", {})
            if result.get("success"):
                out.success("Emulation successful!")
                out.echo(f"  Arch:      {result.get('steps', {}).get('upload', {}).get('arch')}")
                out.echo(f"  Reachable: {probe.get('reachable')}")
                out.echo(f"  HTTP:      {probe.get('http_status')}")
                banner = probe.get('banner', '')
                if banner:
                    out.echo(f"  Banner:    {banner}")
            else:
                out.warning(f"Emulation incomplete: {result.get('error', 'service not reachable')}")
        except Exception as e:
            out.warning(f"Emulation skipped (agent unreachable): {e}")


@fetch_group.command("quick")
@click.argument("query")
@click.option("--emulate", is_flag=True, default=True, help="Auto-emulate after download.")
@click.option("--agent-url", default="http://127.0.0.1:9100", help="Emulation agent URL.")
def fetch_quick(query: str, emulate: bool, agent_url: str) -> None:
    """Search, download, and optionally emulate firmware in one step."""
    from firmware_acquisition import FirmwareAcquisition

    fa = FirmwareAcquisition()
    path = fa.quick(query, emulate=emulate, agent_url=agent_url)

    if path:
        out.success(f"Done! Firmware ready at: {path}")
    else:
        out.error("Failed to acquire firmware")
        raise SystemExit(1)


@fetch_group.command("sources")
def fetch_sources() -> None:
    """List all known firmware sources and their status."""
    from firmware_acquisition import FirmwareAcquisition

    fa = FirmwareAcquisition()
    sources = fa.list_sources()

    click.echo("\nFirmware Sources")
    click.echo("=" * 70)
    for s in sources:
        models = s["models_count"]
        cached = s["cached_entries"]
        click.echo(
            f"  {s['name']:<25}  {models:>3} models  {cached:>4} cached  "
            f"({s['key']})"
        )


@fetch_group.command("cve")
@click.argument("cve_id")
def fetch_cve(cve_id: str) -> None:
    """Search for firmware affected by a CVE."""
    from firmware_acquisition import FirmwareAcquisition

    fa = FirmwareAcquisition()
    results = fa.search(cve_id.upper())

    if not results:
        out.warning(f"No known affected firmware for {cve_id}")
        return

    out.success(f"Found {len(results)} firmware entries for {cve_id}:")
    for r in results:
        out.echo(f"  {r.display_name}  →  {r.url[:100]}")


cli.add_command(fetch_group)


# ---------------------------------------------------------------------------
# Top-level entry point for the module
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for ``python -m emulation_agent.cli``."""
    cli()


if __name__ == "__main__":
    main()
