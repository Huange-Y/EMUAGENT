"""
Emulation Agent Server — FastAPI application.

Main entry point for the firmware emulation service.
Provides REST API for firmware upload, QEMU emulation, service probing,
command execution, and NVRAM configuration.

Start with:
    python -m uvicorn emulation_agent.server:app --host 0.0.0.0 --port 9100
or:
    emu-server start
"""

import os
import time
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("EMU_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("emulation_agent")

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Emulation Agent",
        description="QEMU-based firmware emulation service for vulnerability research",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — allow remote vulnagent and browser tools
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Startup / Shutdown
    # -----------------------------------------------------------------------

    def _ensure_initialized():
        """Lazy-initialize app state (works with TestClient too)."""
        if hasattr(app.state, "config"):
            return
        logger.info("Initializing Emulation Agent v1.0.0 (lazy)")

        # Try package imports first, fall back to direct imports
        try:
            from emulation_agent.config import load_config
            from emulation_agent.firmware_manager import FirmwareManager
            from emulation_agent.qemu_runner import QemuRunner
            from emulation_agent.nvram import NvramEmulator
            from emulation_agent.probe import ProbeManager
        except ModuleNotFoundError:
            from config import load_config
            from firmware_manager import FirmwareManager
            from qemu_runner import QemuRunner
            from nvram import NvramEmulator
            from probe import ProbeManager

        config = load_config()
        config.detect_qemu_binaries()

        # Also set the global config singleton
        try:
            import emulation_agent.config as cfg
        except ModuleNotFoundError:
            import config as cfg
        cfg._config = config

        app.state.firmware_manager = FirmwareManager(config.rootfs_dir)
        app.state.qemu_runner = QemuRunner(
            qemu_binaries=config.available_qemu,
            logs_base_dir=config.logs_dir,
            default_timeout=config.qemu_timeout_default,
        )
        app.state.nvram_emulator = NvramEmulator(config.nvram_templates_dir)
        app.state.probe_manager = ProbeManager(default_timeout=5.0)
        app.state.config = config
        app.state.start_time = time.time()

        logger.info(
            f"Server ready on {config.agent_host}:{config.agent_port}. "
            f"QEMU arches: {list(config.available_qemu.keys())}"
        )

    @app.on_event("startup")
    async def startup():
        """Initialize all components on server start."""
        _ensure_initialized()

    @app.on_event("shutdown")
    async def shutdown():
        """Clean up on server shutdown."""
        logger.info("Shutting down Emulation Agent...")
        if hasattr(app.state, "qemu_runner"):
            app.state.qemu_runner.stop_all_services()
        if hasattr(app.state, "firmware_manager") and app.state.config.cleanup_on_shutdown:
            # Don't delete rootfs on shutdown by default — they may be needed again
            pass
        logger.info("Shutdown complete")

    # -----------------------------------------------------------------------
    # Request / Response models
    # -----------------------------------------------------------------------

    class UploadRequest(BaseModel):
        url: Optional[str] = None
        format: str = "auto"

    class StartServiceRequest(BaseModel):
        rootfs_id: str
        binary_path: Optional[str] = None
        binary_name: Optional[str] = None
        args: Optional[List[str]] = None
        env: Optional[Dict[str, str]] = None
        port: Optional[int] = None
        timeout: int = 30

    class ProbeRequest(BaseModel):
        host: str = "127.0.0.1"
        port: int = 80
        protocol: str = "auto"
        timeout: int = 5

    class ExecRequest(BaseModel):
        rootfs_id: str
        command: str
        timeout: int = 10
        env: Optional[Dict[str, str]] = None

    class NvramConfigRequest(BaseModel):
        rootfs_id: str
        device_type: str = "auto"
        config: Optional[Dict[str, str]] = None

    class DetectArchRequest(BaseModel):
        rootfs_id: str

    # -----------------------------------------------------------------------
    # GET /api/health
    # -----------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        """Check server health and QEMU availability."""
        _ensure_initialized()
        config = app.state.config
        runner = app.state.qemu_runner

        # Check services
        services = runner.list_services()
        running = sum(1 for s in services if s.status == "running")
        crashed = sum(1 for s in services if s.status == "crashed")

        # Determine overall status
        if not config.available_qemu:
            status = "degraded"
        else:
            status = "ok"

        uptime = time.time() - app.state.start_time

        return {
            "status": status,
            "version": "1.0.0",
            "uptime_seconds": round(uptime, 0),
            "qemu_binaries": {
                arch: list(bins.keys())
                for arch, bins in config.available_qemu.items()
            },
            "services": {
                "total": len(services),
                "running": running,
                "crashed": crashed,
            },
            "rootfs_count": len(app.state.firmware_manager.rootfs_registry),
        }

    # -----------------------------------------------------------------------
    # POST /api/upload_rootfs
    # -----------------------------------------------------------------------

    @app.post("/api/upload_rootfs")
    async def upload_rootfs(
        file: Optional[UploadFile] = File(None),
        format: str = Form("auto"),
        url: Optional[str] = Form(None),
    ):
        """Upload and extract a firmware image or rootfs archive.

        Accepts multipart file upload or URL to download from.
        Returns rootfs_id for use with other endpoints.
        """
        _ensure_initialized()
        fm = app.state.firmware_manager

        if file:
            # File upload
            filename = file.filename or "firmware.bin"
            logger.info(f"Receiving upload: {filename} ({file.content_type})")

            # Save to temp file for processing
            with tempfile.NamedTemporaryFile(delete=False, suffix="_" + filename) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            try:
                rootfs_info = fm.extract_rootfs(file_path=tmp_path, format_hint=format)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        elif url:
            # Download from URL
            logger.info(f"Downloading firmware from URL: {url}")
            import urllib.request
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix="_dl") as tmp:
                    urllib.request.urlretrieve(url, tmp.name)
                    tmp_path = tmp.name
                rootfs_info = fm.extract_rootfs(file_path=tmp_path, format_hint=format)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        else:
            raise HTTPException(status_code=400, detail="Either file or url is required")

        return {
            "rootfs_id": rootfs_info.rootfs_id,
            "arch": rootfs_info.arch,
            "arch_confidence": round(rootfs_info.arch_confidence, 2),
            "binary_count": rootfs_info.binary_count,
            "rootfs_path": rootfs_info.rootfs_path,
            "binaries": [
                {
                    "path": b.path,
                    "arch": b.arch,
                    "bits": b.bits,
                    "endian": b.endian,
                    "linked": b.linked,
                    "needed_libs_count": len(b.needed_libs),
                }
                for b in rootfs_info.binaries[:50]  # Return top 50
            ],
            "source_file": rootfs_info.source_file,
        }

    # -----------------------------------------------------------------------
    # POST /api/start_service
    # -----------------------------------------------------------------------

    @app.post("/api/start_service")
    async def start_service(req: StartServiceRequest):
        """Start emulating a firmware binary using QEMU user-mode."""
        _ensure_initialized()
        fm = app.state.firmware_manager
        runner = app.state.qemu_runner
        nvram = app.state.nvram_emulator

        # Get rootfs info
        rootfs_info = fm.get_rootfs(req.rootfs_id)
        if not rootfs_info:
            raise HTTPException(status_code=404, detail=f"Rootfs not found: {req.rootfs_id}")

        # Resolve binary path
        binary_path = req.binary_path
        if not binary_path and req.binary_name:
            binary_path = fm.find_binary(req.rootfs_id, req.binary_name)
            if not binary_path:
                raise HTTPException(
                    status_code=404,
                    detail=f"Binary '{req.binary_name}' not found in rootfs {req.rootfs_id}",
                )
        if not binary_path:
            raise HTTPException(
                status_code=400,
                detail="Either binary_path or binary_name must be provided",
            )

        # Verify binary exists
        abs_binary = os.path.join(rootfs_info.rootfs_path, binary_path.lstrip("/"))
        if not os.path.isfile(abs_binary):
            raise HTTPException(status_code=404, detail=f"Binary not found: {binary_path}")

        # Configure NVRAM if auto mode
        try:
            nvram.write_nvram_config(rootfs_info.rootfs_path, device_type="auto")
        except Exception as e:
            logger.warning(f"NVRAM config write failed (non-fatal): {e}")

        # Get NVRAM environment for QEMU
        nvram_env = nvram.get_qemu_env(rootfs_info.rootfs_path, device_type="auto")

        # Merge user env with NVRAM env (user env takes priority)
        merged_env = {**nvram_env, **(req.env or {})}

        # Start the service
        try:
            service_info = runner.start_user_mode(
                rootfs_path=rootfs_info.rootfs_path,
                binary_path=binary_path,
                arch=rootfs_info.arch,
                rootfs_id=req.rootfs_id,
                args=req.args,
                env=merged_env,
                port=req.port,
                timeout=req.timeout,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        return {
            "service_id": service_info.service_id,
            "rootfs_id": service_info.rootfs_id,
            "binary_name": service_info.binary_name,
            "binary_path": service_info.binary_path,
            "arch": service_info.arch,
            "pid": service_info.pid,
            "port": service_info.port,
            "status": service_info.status,
            "command": " ".join(service_info.command),
            "exit_code": service_info.exit_code,
            "exit_reason": service_info.exit_reason,
            "started_at": service_info.started_at,
            "service_logs": runner.get_service_logs(service_info.service_id, tail=50),
        }

    # -----------------------------------------------------------------------
    # POST /api/stop_service/{service_id}
    # -----------------------------------------------------------------------

    @app.post("/api/stop_service/{service_id}")
    async def stop_service(service_id: str):
        """Stop a running emulated service."""
        _ensure_initialized()
        runner = app.state.qemu_runner
        service = runner.get_service(service_id)
        if not service:
            raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

        stopped = runner.stop_service(service_id)
        return {
            "service_id": service_id,
            "stopped": stopped,
            "exit_code": service.exit_code,
        }

    # -----------------------------------------------------------------------
    # GET /api/services
    # -----------------------------------------------------------------------

    @app.get("/api/services")
    async def list_services(rootfs_id: Optional[str] = None):
        """List all running/past services."""
        _ensure_initialized()
        runner = app.state.qemu_runner
        services = runner.list_services(rootfs_id=rootfs_id)
        return {
            "services": [
                {
                    "service_id": s.service_id,
                    "rootfs_id": s.rootfs_id,
                    "binary_name": s.binary_name,
                    "binary_path": s.binary_path,
                    "arch": s.arch,
                    "pid": s.pid if s.status == "running" else None,
                    "port": s.port,
                    "status": s.status,
                    "started_at": s.started_at,
                    "exit_code": s.exit_code,
                    "exit_reason": s.exit_reason,
                }
                for s in services
            ],
            "total": len(services),
        }

    # -----------------------------------------------------------------------
    # GET /api/services/{service_id}/logs
    # -----------------------------------------------------------------------

    @app.get("/api/services/{service_id}/logs")
    async def get_service_logs(service_id: str, tail: int = 100):
        """Get stdout/stderr logs from a service."""
        _ensure_initialized()
        runner = app.state.qemu_runner
        if not runner.get_service(service_id):
            raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

        logs = runner.get_service_logs(service_id, tail=tail)
        return {
            "service_id": service_id,
            "stdout": logs.get("stdout", ""),
            "stderr": logs.get("stderr", ""),
        }

    # -----------------------------------------------------------------------
    # POST /api/probe
    # -----------------------------------------------------------------------

    @app.post("/api/probe")
    async def probe(req: ProbeRequest):
        """Probe a network service for reachability."""
        _ensure_initialized()
        probe_mgr = app.state.probe_manager
        result = probe_mgr.probe(
            host=req.host,
            port=req.port,
            protocol=req.protocol,
            timeout=req.timeout,
        )
        return result.to_dict()

    # -----------------------------------------------------------------------
    # POST /api/exec
    # -----------------------------------------------------------------------

    @app.post("/api/exec")
    async def exec_command(req: ExecRequest):
        """Execute a command inside the emulated rootfs environment.

        Uses QEMU user-mode + busybox (or the rootfs's own shell) to run
        arbitrary commands in the emulated context.
        """
        _ensure_initialized()
        fm = app.state.firmware_manager
        runner = app.state.qemu_runner
        config = app.state.config

        rootfs_info = fm.get_rootfs(req.rootfs_id)
        if not rootfs_info:
            raise HTTPException(status_code=404, detail=f"Rootfs not found: {req.rootfs_id}")

        # Find a usable shell: busybox sh, /bin/sh, /bin/bash
        shell_path = None
        for candidate in ["bin/busybox", "bin/sh", "bin/bash", "usr/bin/busybox"]:
            cand_abs = os.path.join(rootfs_info.rootfs_path, candidate)
            if os.path.isfile(cand_abs) and os.access(cand_abs, os.X_OK):
                shell_path = candidate
                break

        if not shell_path:
            # Try using /bin/sh via QEMU's built-in handling
            shell_path = "bin/sh"

        # If busybox, wrap with 'sh -c'
        if "busybox" in shell_path:
            args = ["sh", "-c", req.command]
        elif shell_path.endswith("sh") or shell_path.endswith("bash"):
            args = ["-c", req.command]
        else:
            args = ["-c", req.command]

        try:
            service_info = runner.start_user_mode(
                rootfs_path=rootfs_info.rootfs_path,
                binary_path=shell_path,
                arch=rootfs_info.arch,
                rootfs_id=req.rootfs_id,
                args=args,
                env=req.env,
                timeout=req.timeout,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Wait for completion
        import time as _time
        deadline = _time.time() + req.timeout
        while _time.time() < deadline:
            if not runner.is_service_alive(service_info.service_id):
                break
            _time.sleep(0.2)

        # Force stop if still running
        if runner.is_service_alive(service_info.service_id):
            runner.stop_service(service_info.service_id, force=True)

        # Get output
        logs = runner.get_service_logs(service_info.service_id, tail=1000)

        timed_out = runner.is_service_alive(service_info.service_id)

        return {
            "stdout": logs.get("stdout", ""),
            "stderr": logs.get("stderr", ""),
            "exit_code": service_info.exit_code,
            "timed_out": timed_out,
        }

    # -----------------------------------------------------------------------
    # POST /api/nvram_config
    # -----------------------------------------------------------------------

    @app.post("/api/nvram_config")
    async def nvram_config(req: NvramConfigRequest):
        """Configure NVRAM values for a rootfs.

        _ensure_initialized()
        Auto-detects device type and writes NVRAM configuration files
        needed by services like GoAhead, boa, etc.
        """
        fm = app.state.firmware_manager
        nvram = app.state.nvram_emulator

        rootfs_info = fm.get_rootfs(req.rootfs_id)
        if not rootfs_info:
            raise HTTPException(status_code=404, detail=f"Rootfs not found: {req.rootfs_id}")

        config_values = nvram.generate_config(
            rootfs_info.rootfs_path,
            device_type=req.device_type,
            overrides=req.config,
        )

        config_path = nvram.write_nvram_config(
            rootfs_info.rootfs_path,
            device_type=req.device_type,
            overrides=req.config,
        )

        return {
            "success": True,
            "device_type": req.device_type,
            "config_path": config_path,
            "values": config_values,
            "templates_available": nvram.list_templates(),
        }

    # -----------------------------------------------------------------------
    # POST /api/detect_arch
    # -----------------------------------------------------------------------

    @app.post("/api/detect_arch")
    async def detect_arch(req: DetectArchRequest):
        """Re-detect architecture of a rootfs."""
        _ensure_initialized()
        fm = app.state.firmware_manager
        rootfs_info = fm.get_rootfs(req.rootfs_id)
        if not rootfs_info:
            raise HTTPException(status_code=404, detail=f"Rootfs not found: {req.rootfs_id}")

        arch, confidence = fm.detect_architecture(rootfs_info.rootfs_path)

        # Sample some binaries to show what we found
        samples = []
        for b in rootfs_info.binaries[:5]:
            samples.append({
                "path": b.path,
                "arch": b.arch,
                "bits": b.bits,
                "endian": b.endian,
            })

        return {
            "arch": arch,
            "confidence": round(confidence, 2),
            "binary_samples": samples,
        }

    # -----------------------------------------------------------------------
    # DELETE /api/rootfs/{rootfs_id}
    # -----------------------------------------------------------------------

    @app.delete("/api/rootfs/{rootfs_id}")
    async def delete_rootfs(rootfs_id: str):
        """Delete an extracted rootfs and stop all associated services."""
        _ensure_initialized()
        runner = app.state.qemu_runner
        fm = app.state.firmware_manager

        # Stop all services using this rootfs
        stopped = 0
        for s in runner.list_services(rootfs_id=rootfs_id):
            if runner.stop_service(s.service_id):
                stopped += 1

        # Delete the rootfs
        deleted = fm.delete_rootfs(rootfs_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Rootfs not found: {rootfs_id}")

        return {
            "success": True,
            "stopped_services": stopped,
        }

    # -----------------------------------------------------------------------
    # GET /api/rootfs
    # -----------------------------------------------------------------------

    @app.get("/api/rootfs")
    async def list_rootfs():
        """List all extracted rootfs."""
        _ensure_initialized()
        fm = app.state.firmware_manager
        rootfs_list = fm.list_rootfs()
        return {
            "rootfs": [
                {
                    "rootfs_id": r.rootfs_id,
                    "arch": r.arch,
                    "arch_confidence": round(r.arch_confidence, 2),
                    "binary_count": r.binary_count,
                    "rootfs_path": r.rootfs_path,
                    "created_at": r.created_at,
                    "source_file": r.source_file,
                }
                for r in rootfs_list
            ],
            "total": len(rootfs_list),
        }

    # -----------------------------------------------------------------------
    # GET / — redirect to docs
    # -----------------------------------------------------------------------

    @app.get("/")
    async def root():
        """Redirect to API documentation."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/docs")

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (for uvicorn)
# ---------------------------------------------------------------------------
app = create_app()
