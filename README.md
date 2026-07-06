# EMUAGENT — QEMU Firmware Emulation Backend

REST API for firmware binary emulation. Upload rootfs, launch QEMU services, probe endpoints.

## Quick Start
```bash
pip install -r requirements.txt
python3 -m uvicorn server:app --host 0.0.0.0 --port 9100
```

## API
| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/health | Health check |
| POST | /api/upload_rootfs | Upload rootfs tarball |
| POST | /api/detect_arch | Auto-detect architecture |
| POST | /api/start_service | Start binary under QEMU |
| POST | /api/probe | Probe emulated service |
| POST | /api/exec | Execute command in emulated env |
| GET | /api/services | List running services |
| GET | /api/rootfs | List available rootfs images |

## License
MIT
