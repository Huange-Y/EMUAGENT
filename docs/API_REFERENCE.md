# Emulation Agent API Reference

> Complete REST API reference for the Emulation Agent firmware emulation service.
>
> 固件模拟服务完整REST API参考

---

## Table of Contents

1. [Overview](#overview)
2. [Conventions](#conventions)
3. [Endpoints](#endpoints)
   - [GET /api/health](#get-apihealth)
   - [POST /api/upload_rootfs](#post-apiupload_rootfs)
   - [POST /api/upload_firmware](#post-apiupload_firmware)
   - [POST /api/start_service](#post-apistart_service)
   - [GET /api/service/{service_id}](#get-apiserviceservice_id)
   - [POST /api/stop_service/{service_id}](#post-apistop_serviceservice_id)
   - [GET /api/services](#get-apiservices)
   - [POST /api/probe](#post-apiprobe)
   - [POST /api/exec](#post-apiexec)
   - [POST /api/nvram_config](#post-apinvram_config)
   - [GET /api/architectures](#get-apiarchitectures)
4. [Common Response Formats](#common-response-formats)
5. [Status Codes](#status-codes)
6. [Rate Limiting](#rate-limiting)
7. [Versioning Policy](#versioning-policy)

---

## Overview

- **Base URL**: `http://{host}:{port}` (default: `http://localhost:9100`)
- **Content Type**: `application/json` for request/response bodies; `multipart/form-data` for file uploads
- **Authentication**: None (designed for internal network use; use SSH tunneling for secure access)
- **API Version**: v1 (no version prefix in URL path currently)

### Health Check Before Starting

```bash
curl http://localhost:9100/api/health
```

---

## Conventions

### Request Format

All request bodies must be JSON with `Content-Type: application/json` header, except file upload endpoints which use `multipart/form-data`.

### Response Format

Successful responses follow this structure:

```json
{
  "status": "ok",
  ... endpoint-specific fields ...
}
```

Error responses follow this structure:

```json
{
  "error": true,
  "error_type": "ErrorTypeName",
  "message": "Human-readable error description",
  "detail": {
    ... optional additional context ...
  }
}
```

### Architecture Naming

Architecture strings use the following canonical forms:

| String | Description |
|--------|-------------|
| `mips` | MIPS big-endian, 32-bit |
| `mipsel` | MIPS little-endian, 32-bit |
| `arm` | ARMv7, 32-bit |
| `aarch64` | AArch64 (ARM 64-bit) |
| `i386` | x86, 32-bit |
| `x86_64` | x86-64, 64-bit |

### Port Range

The service allocates ports from the range specified in configuration (default: 8000-9000).

---

## Endpoints

### GET /api/health

Returns health status of the Emulation Agent, including QEMU binary availability and active services.

**Request:**

No parameters required.

**Response Schema:**

```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "version": "1.0.0",
  "qemu_binaries": {
    "mips": true,
    "mipsel": true,
    "arm": true,
    "aarch64": true,
    "x86_64": true
  },
  "services": {
    "running": 3,
    "crashed": 1,
    "total": 4
  },
  "storage": {
    "rootfs_count": 5,
    "rootfs_disk_mb": 450
  },
  "ports": {
    "range": "8000-9000",
    "used": 3,
    "available": 997
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` when server is running |
| `uptime_seconds` | integer | Server uptime in seconds |
| `version` | string | Server version string |
| `qemu_binaries.{arch}` | boolean | Whether each QEMU binary is available |
| `services.running` | integer | Number of currently running services |
| `services.crashed` | integer | Number of crashed-but-not-cleaned services |
| `services.total` | integer | Total tracked services |
| `storage.rootfs_count` | integer | Number of uploaded root filesystems |
| `storage.rootfs_disk_mb` | integer | Total disk usage for rootfs storage (MB) |
| `ports.range` | string | Configured port allocation range |
| `ports.used` | integer | Currently allocated ports |
| `ports.available` | integer | Free ports in range |

**Example Request:**

```bash
curl http://localhost:9100/api/health
```

**Example Response:**

```json
{
  "status": "ok",
  "uptime_seconds": 14420,
  "version": "1.0.0",
  "qemu_binaries": {
    "mips": true,
    "mipsel": true,
    "arm": true,
    "aarch64": true,
    "x86_64": true
  },
  "services": {
    "running": 2,
    "crashed": 0,
    "total": 2
  },
  "storage": {
    "rootfs_count": 3,
    "rootfs_disk_mb": 280
  },
  "ports": {
    "range": "8000-9000",
    "used": 2,
    "available": 998
  }
}
```

---

### POST /api/upload_rootfs

Uploads a pre-extracted root filesystem archive (tar.gz format). The server extracts it, detects the architecture, and stores it for later emulation.

**Request:**

Multipart form data:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File (binary) | Yes | Root filesystem archive in `.tar.gz` format |

**Constraints:**
- Maximum file size: `EMU_MAX_ROOTFS_SIZE_MB` (default: 2048 MB)
- Format: gzip-compressed tar archive (`.tar.gz`)
- Must contain ELF binaries for architecture detection

**Response Schema (Success):**

```json
{
  "rootfs_id": "3f8a2b1c9d4e",
  "arch": "mipsel",
  "file_count": 1543,
  "status": "ok",
  "warning": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `rootfs_id` | string | Unique identifier for this root filesystem (UUID or hash) |
| `arch` | string | Detected architecture (e.g., `"mipsel"`, `"arm"`) |
| `file_count` | integer | Number of files extracted |
| `status` | string | `"ok"` on success |
| `warning` | string or null | Warning message if non-critical issues (e.g., broken symlinks) |

**Response Schema (Error):**

```json
{
  "error": true,
  "error_type": "ExtractionError",
  "message": "Failed to extract archive: not a valid tar.gz file",
  "detail": {
    "file_name": "bad_file.bin",
    "detected_type": "data",
    "expected_types": ["gzip compressed data"]
  }
}
```

| Error Type | HTTP Status | Description |
|-----------|-------------|-------------|
| `ValidationError` | 400 | File missing or too large |
| `ExtractionError` | 400 | Invalid archive format or extraction failure |
| `ArchitectureError` | 400 | No ELF binaries found in extracted rootfs |
| `StorageError` | 500 | Disk full or permission error |
| `InternalError` | 500 | Unexpected server error |

**Example Request:**

```bash
curl -X POST http://localhost:9100/api/upload_rootfs \
  -F "file=@squashfs-root.tar.gz"
```

**Example Response (Success):**

```json
{
  "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
  "arch": "mipsel",
  "file_count": 1543,
  "status": "ok",
  "warning": null
}
```

**Example Response (Error - No ELF binaries):**

```json
{
  "error": true,
  "error_type": "ArchitectureError",
  "message": "No ELF binaries found in the uploaded rootfs. Cannot determine architecture.",
  "detail": {
    "rootfs_id": "3f8a2b1c9d4e",
    "files_checked": 1543,
    "non_elf_files": 1543,
    "suggestion": "Check that the rootfs contains executable binaries. The archive should contain /bin, /sbin, /usr/bin directories."
  }
}
```

**Notes:**
- The rootfs is stored at `./rootfs/<rootfs_id>/` on the server.
- A `metadata.json` file is created alongside the rootfs with architecture and timestamp info.
- Rootfs may be cleaned up after the configured TTL (default: 24 hours).

---

### POST /api/upload_firmware

Uploads a raw firmware image. The server will attempt to extract it using binwalk automatically.

**Request:**

Multipart form data:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File (binary) | Yes | Raw firmware image (any format binwalk can handle) |
| `extract_all` | boolean | No | Extract all file systems found (default: `true`) |

**Response Schema (Success):**

```json
{
  "rootfs_ids": ["3f8a2b1c", "7d5e3f1a"],
  "primary_rootfs_id": "3f8a2b1c",
  "arch": "mipsel",
  "extraction_method": "binwalk",
  "filesystems_found": ["squashfs", "jffs2"],
  "status": "ok"
}
```

**Example Request:**

```bash
curl -X POST http://localhost:9100/api/upload_firmware \
  -F "file=@DIR-882_FW120.bin"
```

**Example Response:**

```json
{
  "rootfs_ids": ["3f8a2b1c9d4e5f6a"],
  "primary_rootfs_id": "3f8a2b1c9d4e5f6a",
  "arch": "mipsel",
  "extraction_method": "binwalk",
  "filesystems_found": ["squashfs"],
  "status": "ok"
}
```

---

### POST /api/start_service

Starts a binary from the uploaded rootfs in QEMU user-mode emulation.

**Request:**

```json
{
  "rootfs_id": "3f8a2b1c",
  "binary_path": "/usr/sbin/httpd",
  "binary_name": "httpd",
  "args": ["-p", "8080"],
  "port": 8080,
  "env_vars": {
    "LD_LIBRARY_PATH": "/lib:/usr/lib",
    "HOME": "/"
  },
  "strace": false,
  "timeout": 30
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `rootfs_id` | string | Yes | - | Root filesystem identifier |
| `binary_path` | string | Yes | - | Path to binary within rootfs (e.g., `/usr/sbin/httpd`) |
| `binary_name` | string | No | basename of binary_path | Logical name for the binary |
| `args` | string[] | No | `[]` | Command-line arguments for the binary |
| `port` | integer | No | Auto-allocated | TCP port the service should bind to |
| `env_vars` | object | No | `{}` | Environment variables to set |
| `strace` | boolean | No | `false` | Enable QEMU strace output |
| `timeout` | integer | No | `300` | Seconds to wait for service to become healthy |

**Constraints:**
- `binary_path` must exist within the rootfs
- `port` must be within the allocated port range and not in use
- `rootfs_id` must reference an existing uploaded rootfs

**Response Schema (Success):**

```json
{
  "service_id": "svc-7a3f9b2c",
  "rootfs_id": "3f8a2b1c",
  "pid": 45821,
  "port": 8080,
  "status": "running",
  "binary_name": "httpd",
  "arch": "mipsel",
  "qemu_binary": "qemu-mipsel-static",
  "started_at": "2026-07-02T10:30:00.123Z",
  "command": "qemu-mipsel-static -L ./rootfs/3f8a2b1c/ ./rootfs/3f8a2b1c/usr/sbin/httpd -p 8080"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `service_id` | string | Unique service identifier |
| `rootfs_id` | string | Parent rootfs identifier |
| `pid` | integer | Host OS process ID of the QEMU process |
| `port` | integer | TCP port the service is bound to |
| `status` | string | `"running"`, `"starting"`, or `"crashed"` |
| `binary_name` | string | Name of the emulated binary |
| `arch` | string | Target architecture |
| `qemu_binary` | string | QEMU binary used for emulation |
| `started_at` | string | ISO 8601 timestamp of service start |
| `command` | string | Full command line used to start QEMU (for debugging) |

**Response Schema (Crashed):**

```json
{
  "service_id": "svc-7a3f9b2c",
  "pid": null,
  "port": 8080,
  "status": "crashed",
  "exit_code": -11,
  "diagnosis": "SIGSEGV: The binary crashed with a segmentation fault. This is common when NVRAM configuration is missing. Try configuring NVRAM via /api/nvram_config.",
  "stderr_tail": "Error loading shared library libnvram.so: No such file or directory\nSegmentation fault\n"
}
```

| Error Type | HTTP Status | Description |
|-----------|-------------|-------------|
| `ValidationError` | 400 | Missing required fields or invalid values |
| `NotFoundError` | 404 | `rootfs_id` not found |
| `PortConflictError` | 409 | Requested port is already in use |
| `PortExhaustionError` | 500 | No free ports in auto-allocation range |
| `QemuNotFoundError` | 500 | QEMU binary for detected architecture not found |

**Example Request:**

```bash
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "binary_path": "/usr/sbin/httpd",
    "binary_name": "httpd",
    "args": ["-p", "8080"],
    "port": 8080,
    "env_vars": {
      "LD_LIBRARY_PATH": "/lib:/usr/lib:/usr/local/lib"
    }
  }'
```

**Example Response:**

```json
{
  "service_id": "svc-7a3f9b2c1d5e",
  "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
  "pid": 45821,
  "port": 8080,
  "status": "running",
  "binary_name": "httpd",
  "arch": "mipsel",
  "qemu_binary": "qemu-mipsel-static",
  "started_at": "2026-07-02T10:30:00.123456Z",
  "command": "qemu-mipsel-static -L ./rootfs/3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c/ ./rootfs/3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c/usr/sbin/httpd -p 8080"
}
```

---

### GET /api/service/{service_id}

Returns the current status and metadata for a specific emulated service.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `service_id` | string | Service identifier returned by `/api/start_service` |

**Response Schema:**

```json
{
  "service_id": "svc-7a3f9b2c",
  "rootfs_id": "3f8a2b1c",
  "pid": 45821,
  "port": 8080,
  "status": "running",
  "binary_name": "httpd",
  "binary_path": "/usr/sbin/httpd",
  "arch": "mipsel",
  "qemu_binary": "qemu-mipsel-static",
  "started_at": "2026-07-02T10:30:00.123Z",
  "uptime_seconds": 3600,
  "exit_code": null,
  "stdout_tail": "httpd: listening on port 8080\nhttpd: ready to accept connections\n",
  "stderr_tail": "",
  "strace_enabled": false,
  "env_vars": {
    "LD_LIBRARY_PATH": "/lib:/usr/lib"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `service_id` | string | Service identifier |
| `rootfs_id` | string | Parent rootfs identifier |
| `pid` | integer or null | Process ID (null if service has exited) |
| `port` | integer | Allocated port |
| `status` | string | Current state: `"running"`, `"crashed"`, `"stopped"`, `"dead"` |
| `binary_name` | string | Emulated binary name |
| `binary_path` | string | Path to binary within rootfs |
| `arch` | string | Target architecture |
| `qemu_binary` | string | QEMU binary used |
| `started_at` | string | ISO 8601 start timestamp |
| `uptime_seconds` | integer | Seconds since service start |
| `exit_code` | integer or null | Exit code (null if still running) |
| `stdout_tail` | string | Last 64KB of stdout output |
| `stderr_tail` | string | Last 64KB of stderr output |
| `strace_enabled` | boolean | Whether strace was enabled |
| `env_vars` | object | Environment variables passed to QEMU |

**Example Request:**

```bash
curl http://localhost:9100/api/service/svc-7a3f9b2c1d5e
```

**Example Response:**

```json
{
  "service_id": "svc-7a3f9b2c1d5e",
  "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
  "pid": 45821,
  "port": 8080,
  "status": "running",
  "binary_name": "httpd",
  "binary_path": "/usr/sbin/httpd",
  "arch": "mipsel",
  "qemu_binary": "qemu-mipsel-static",
  "started_at": "2026-07-02T10:30:00.123456Z",
  "uptime_seconds": 3600,
  "exit_code": null,
  "stdout_tail": "httpd: listening on port 8080\nhttpd: accepted connection from 127.0.0.1:54321\n",
  "stderr_tail": "",
  "strace_enabled": false,
  "env_vars": {
    "LD_LIBRARY_PATH": "/lib:/usr/lib:/usr/local/lib"
  }
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 404 | Service with given `service_id` not found |

---

### POST /api/stop_service/{service_id}

Stops a running emulated service and releases its resources.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `service_id` | string | Service identifier |

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `force` | boolean | No | `false` | If true, sends SIGKILL immediately instead of SIGTERM then SIGKILL |

**Response Schema:**

```json
{
  "service_id": "svc-7a3f9b2c",
  "status": "stopped",
  "exit_code": -15,
  "port_released": 8080,
  "uptime_seconds": 3600,
  "force_killed": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `service_id` | string | Service identifier |
| `status` | string | `"stopped"` |
| `exit_code` | integer | Exit code of the killed process |
| `port_released` | integer | Port that was released back to the pool |
| `uptime_seconds` | integer | Total uptime before stop |
| `force_killed` | boolean | Whether force kill was used |

**Example Request:**

```bash
curl -X POST http://localhost:9100/api/stop_service/svc-7a3f9b2c1d5e
```

**Example Response:**

```json
{
  "service_id": "svc-7a3f9b2c1d5e",
  "status": "stopped",
  "exit_code": -15,
  "port_released": 8080,
  "uptime_seconds": 3600,
  "force_killed": false
}
```

**Example Request (Force Kill):**

```bash
curl -X POST "http://localhost:9100/api/stop_service/svc-7a3f9b2c1d5e?force=true"
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 404 | Service with given `service_id` not found |
| 409 | Service is not in a running state |

---

### GET /api/services

Lists all tracked services.

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `status` | string | No | - | Filter by status: `"running"`, `"crashed"`, `"stopped"`, `"all"` |
| `rootfs_id` | string | No | - | Filter by rootfs identifier |

**Response Schema:**

```json
{
  "services": [
    {
      "service_id": "svc-7a3f9b2c",
      "rootfs_id": "3f8a2b1c",
      "binary_name": "httpd",
      "status": "running",
      "port": 8080,
      "pid": 45821,
      "arch": "mipsel",
      "started_at": "2026-07-02T10:30:00Z",
      "uptime_seconds": 3600
    },
    {
      "service_id": "svc-8b4g0c3d",
      "rootfs_id": "3f8a2b1c",
      "binary_name": "telnetd",
      "status": "running",
      "port": 2323,
      "pid": 45822,
      "arch": "mipsel",
      "started_at": "2026-07-02T10:31:00Z",
      "uptime_seconds": 3540
    }
  ],
  "count": 2,
  "total_running": 2,
  "total_crashed": 0,
  "total_stopped": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `services` | array | List of service summaries |
| `count` | integer | Number of services in response (respects filters) |
| `total_running` | integer | Total running services |
| `total_crashed` | integer | Total crashed services |
| `total_stopped` | integer | Total stopped services |

**Example Request:**

```bash
# List all services
curl http://localhost:9100/api/services

# List only running services
curl "http://localhost:9100/api/services?status=running"

# List services for a specific rootfs
curl "http://localhost:9100/api/services?rootfs_id=3f8a2b1c9d4e"
```

**Example Response:**

```json
{
  "services": [
    {
      "service_id": "svc-7a3f9b2c1d5e",
      "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
      "binary_name": "httpd",
      "status": "running",
      "port": 8080,
      "pid": 45821,
      "arch": "mipsel",
      "started_at": "2026-07-02T10:30:00.123456Z",
      "uptime_seconds": 3600
    }
  ],
  "count": 1,
  "total_running": 1,
  "total_crashed": 0,
  "total_stopped": 0
}
```

---

### POST /api/probe

Probes an emulated service to verify it is reachable and responding correctly. Supports TCP connect, HTTP request, and Telnet banner detection.

**Request:**

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "protocol": "http",
  "http_path": "/",
  "timeout": 10
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `host` | string | Yes | - | Target host (typically `"127.0.0.1"`) |
| `port` | integer | Yes | - | Target port |
| `protocol` | string | Yes | - | Probe protocol: `"tcp"`, `"http"`, or `"telnet"` |
| `http_path` | string | No | `"/"` | HTTP request path (only used if protocol is `"http"`) |
| `timeout` | integer | No | `10` | Connection/read timeout in seconds |

**Response Schema (TCP):**

```json
{
  "protocol": "tcp",
  "host": "127.0.0.1",
  "port": 8080,
  "reachable": true,
  "latency_ms": 1.23
}
```

**Response Schema (HTTP):**

```json
{
  "protocol": "http",
  "host": "127.0.0.1",
  "port": 8080,
  "reachable": true,
  "http_status": 200,
  "http_headers": {
    "Server": "GoAhead-Webs",
    "Content-Type": "text/html",
    "Content-Length": "1234"
  },
  "http_body_preview": "<html><head><title>D-Link DIR-882</title>..."
}
```

**Response Schema (Telnet):**

```json
{
  "protocol": "telnet",
  "host": "127.0.0.1",
  "port": 2323,
  "reachable": true,
  "banner": "BusyBox v1.22.1 (2024-01-15 10:30:00 CST) built-in shell (ash)\r\nEnter 'help' for a list of built-in commands.\r\n\r\n~ # ",
  "banner_lines": 3
}
```

**Common Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `protocol` | string | Protocol used for probe |
| `host` | string | Target host |
| `port` | integer | Target port |
| `reachable` | boolean | Whether the service is reachable |

**Protocol-Specific Fields:**

| Field | Type | Protocol | Description |
|-------|------|----------|-------------|
| `latency_ms` | float | tcp | Connection latency in milliseconds |
| `http_status` | integer | http | HTTP response status code |
| `http_headers` | object | http | Response headers as key-value pairs |
| `http_body_preview` | string | http | First 512 characters of response body |
| `banner` | string | telnet | Full telnet banner text received |
| `banner_lines` | integer | telnet | Number of lines in the banner |

**Response (Unreachable):**

```json
{
  "protocol": "tcp",
  "host": "127.0.0.1",
  "port": 8080,
  "reachable": false,
  "error": "Connection refused",
  "suggestion": "The service may not have started yet. Check service status with GET /api/service/{service_id}."
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 400 | Invalid protocol specified |
| 422 | Missing required fields |
| 504 | Probe timeout |

**Example Requests:**

```bash
# TCP probe
curl -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 8080, "protocol": "tcp"}'

# HTTP probe with custom path
curl -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{
    "host": "127.0.0.1",
    "port": 8080,
    "protocol": "http",
    "http_path": "/login.asp"
  }'

# Telnet probe
curl -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 2323, "protocol": "telnet"}'
```

**Example Response (HTTP):**

```json
{
  "protocol": "http",
  "host": "127.0.0.1",
  "port": 8080,
  "reachable": true,
  "http_status": 200,
  "http_headers": {
    "Server": "GoAhead-Webs",
    "Content-Type": "text/html",
    "Content-Length": "4521",
    "Connection": "close"
  },
  "http_body_preview": "<html>\n<head>\n<title>D-Link DIR-882</title>\n<meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\">\n</head>\n<body>\n<div id=\"header\">\n<h1>DIR-882</h1>\n</div>\n..."
}
```

---

### POST /api/exec

Executes a shell command inside the emulated rootfs environment using QEMU user-mode and busybox.

**Request:**

```json
{
  "rootfs_id": "3f8a2b1c",
  "command": "ls -la /usr/sbin/",
  "timeout": 10,
  "env_vars": {}
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `rootfs_id` | string | Yes | - | Root filesystem identifier |
| `command` | string | Yes | - | Shell command to execute |
| `timeout` | integer | No | `30` | Maximum execution time in seconds |
| `env_vars` | object | No | `{}` | Additional environment variables |

**Constraints:**
- `timeout` maximum: 600 seconds (10 minutes)
- `command` maximum length: 4096 characters
- The command is executed via `busybox sh -c "<command>"` within the rootfs

**Response Schema:**

```json
{
  "exit_code": 0,
  "stdout": "total 1234\n-rwxr-xr-x  1 root root  12345 Jan 15  2024 httpd\n-rwxr-xr-x  1 root root  54321 Jan 15  2024 telnetd\n",
  "stderr": "",
  "command": "ls -la /usr/sbin/",
  "rootfs_id": "3f8a2b1c",
  "execution_time_ms": 1234,
  "timed_out": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `exit_code` | integer | Exit code of the command |
| `stdout` | string | Standard output of the command |
| `stderr` | string | Standard error of the command |
| `command` | string | The command that was executed |
| `rootfs_id` | string | Root filesystem used |
| `execution_time_ms` | integer | Actual execution time in milliseconds |
| `timed_out` | boolean | Whether the command timed out |

**Response (Timeout):**

```json
{
  "exit_code": -1,
  "stdout": "partial output before timeout...",
  "stderr": "",
  "command": "find / -name '*.conf'",
  "rootfs_id": "3f8a2b1c",
  "execution_time_ms": 10001,
  "timed_out": true
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 400 | Invalid or empty command |
| 404 | `rootfs_id` not found |
| 504 | Command execution timed out |
| 500 | QEMU binary not found for rootfs architecture |

**Example Requests:**

```bash
# List files in a directory
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "ls -la /usr/sbin/",
    "timeout": 10
  }'

# Check binary dependencies
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "ldd /usr/sbin/httpd 2>&1; file /usr/sbin/httpd",
    "timeout": 10
  }'

# Read a config file
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "cat /etc/passwd",
    "timeout": 5
  }'

# Find ELF binaries
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "find / -type f | while read f; do file \"$f\" | grep -q ELF && echo \"$f\"; done | head -20",
    "timeout": 30
  }'

# Check for NVRAM references in binary
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "strings /usr/sbin/httpd | grep -i nvram | head -30",
    "timeout": 10
  }'
```

**Example Response:**

```json
{
  "exit_code": 0,
  "stdout": "total 560\n-rwxr-xr-x  1 0  0  45678 Jan 15  2024 httpd\n-rwxr-xr-x  1 0  0  23456 Jan 15  2024 telnetd\n-rwxr-xr-x  1 0  0  12345 Jan 15  2024 upnpd\n-rwxr-xr-x  1 0  0   9876 Jan 15  2024 dnsmasq\n",
  "stderr": "",
  "command": "ls -la /usr/sbin/",
  "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
  "execution_time_ms": 845,
  "timed_out": false
}
```

---

### POST /api/nvram_config

Configures the NVRAM simulation for a specific root filesystem. This writes a key-value configuration file that emulated binaries can read to obtain device configuration parameters.

**Request:**

```json
{
  "rootfs_id": "3f8a2b1c",
  "config": {
    "lan_ipaddr": "192.168.0.1",
    "lan_netmask": "255.255.255.0",
    "http_port": "80",
    "http_username": "admin",
    "http_password": "admin",
    "product_name": "DIR-882",
    "firmware_version": "1.20"
  },
  "merge": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `rootfs_id` | string | Yes | - | Root filesystem identifier |
| `config` | object | Yes | - | Key-value pairs for NVRAM configuration |
| `merge` | boolean | No | `true` | If true, merge with existing config; if false, replace entirely |

**Constraints:**
- Keys must be alphanumeric with underscores (regex: `^[a-zA-Z0-9_]+$`)
- Values must be strings (max 1024 characters each)
- Maximum 500 key-value pairs per request

**Response Schema:**

```json
{
  "status": "ok",
  "rootfs_id": "3f8a2b1c",
  "path": "./rootfs/3f8a2b1c/etc_ro/nvram.conf",
  "config_count": 8,
  "config_keys": ["lan_ipaddr", "lan_netmask", "http_port", "http_username", "http_password", "product_name", "firmware_version", "telnet_enable"],
  "merged": true,
  "previous_config_exists": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` on success |
| `rootfs_id` | string | Root filesystem identifier |
| `path` | string | File path where NVRAM config was written |
| `config_count` | integer | Number of key-value pairs in the config |
| `config_keys` | string[] | Keys that were set |
| `merged` | boolean | Whether config was merged with existing |
| `previous_config_exists` | boolean | Whether a config file already existed |

**Location:** The NVRAM config is written to `<rootfs>/etc_ro/nvram.conf` in standard `KEY=VALUE` format, one per line.

**Example Request:**

```bash
curl -X POST http://localhost:9100/api/nvram_config \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "config": {
      "lan_ipaddr": "192.168.0.1",
      "lan_netmask": "255.255.255.0",
      "http_port": "80",
      "http_username": "admin",
      "http_password": "admin",
      "product_name": "DIR-882",
      "firmware_version": "1.20",
      "telnet_enable": "1"
    }
  }'
```

**Example Response:**

```json
{
  "status": "ok",
  "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
  "path": "./rootfs/3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c/etc_ro/nvram.conf",
  "config_count": 8,
  "config_keys": [
    "lan_ipaddr",
    "lan_netmask",
    "http_port",
    "http_username",
    "http_password",
    "product_name",
    "firmware_version",
    "telnet_enable"
  ],
  "merged": true,
  "previous_config_exists": false
}
```

**Example Request (Reading Current NVRAM Config -- use /api/exec):**

```bash
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{
    "rootfs_id": "3f8a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "command": "cat /etc_ro/nvram.conf 2>/dev/null || echo \"No NVRAM config found\"",
    "timeout": 5
  }'
```

**Example Request (Using a Pre-built Template for Common Devices):**

```bash
# TODO: If template endpoint is implemented
# curl -X POST http://localhost:9100/api/nvram_config \
#   -H "Content-Type: application/json" \
#   -d '{"rootfs_id": "...", "template": "dlink_router_a1"}'
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 404 | `rootfs_id` not found |
| 400 | Invalid key name or too many config entries |
| 500 | Could not write configuration file |

---

### GET /api/architectures

Lists all supported architectures and their QEMU binary status.

**Request:**

No parameters required.

**Response Schema:**

```json
{
  "architectures": [
    {
      "name": "mips",
      "description": "MIPS big-endian, 32-bit",
      "qemu_binary": "qemu-mips-static",
      "available": true
    },
    {
      "name": "mipsel",
      "description": "MIPS little-endian, 32-bit",
      "qemu_binary": "qemu-mipsel-static",
      "available": true
    },
    {
      "name": "arm",
      "description": "ARMv7, 32-bit",
      "qemu_binary": "qemu-arm-static",
      "available": true
    },
    {
      "name": "aarch64",
      "description": "AArch64 (ARM 64-bit)",
      "qemu_binary": "qemu-aarch64-static",
      "available": true
    },
    {
      "name": "i386",
      "description": "x86, 32-bit",
      "qemu_binary": "qemu-i386-static",
      "available": false
    },
    {
      "name": "x86_64",
      "description": "x86-64, 64-bit",
      "qemu_binary": "qemu-x86_64-static",
      "available": true
    }
  ],
  "default_architecture": "mipsel",
  "common_iot_architectures": ["mipsel", "mips", "arm", "aarch64"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `architectures` | array | List of architecture objects |
| `architectures[].name` | string | Canonical architecture name |
| `architectures[].description` | string | Human-readable description |
| `architectures[].qemu_binary` | string | Expected QEMU binary name |
| `architectures[].available` | boolean | Whether the QEMU binary is found on the system |
| `default_architecture` | string | Fallback architecture if detection is ambiguous |
| `common_iot_architectures` | string[] | Architectures most commonly found in IoT devices |

**Example Request:**

```bash
curl http://localhost:9100/api/architectures
```

**Example Response:**

```json
{
  "architectures": [
    {"name": "mips", "description": "MIPS big-endian, 32-bit", "qemu_binary": "qemu-mips-static", "available": true},
    {"name": "mipsel", "description": "MIPS little-endian, 32-bit", "qemu_binary": "qemu-mipsel-static", "available": true},
    {"name": "arm", "description": "ARMv7, 32-bit", "qemu_binary": "qemu-arm-static", "available": true},
    {"name": "aarch64", "description": "AArch64 (ARM 64-bit)", "qemu_binary": "qemu-aarch64-static", "available": true},
    {"name": "i386", "description": "x86, 32-bit", "qemu_binary": "qemu-i386-static", "available": false},
    {"name": "x86_64", "description": "x86-64, 64-bit", "qemu_binary": "qemu-x86_64-static", "available": true}
  ],
  "default_architecture": "mipsel",
  "common_iot_architectures": ["mipsel", "mips", "arm", "aarch64"]
}
```

---

## Common Response Formats

### Success

All successful responses include a `"status": "ok"` field (except `/api/health` which uses `"status": "ok"` as well). Specific data is included alongside the status field.

### Client Error (4xx)

```json
{
  "error": true,
  "error_type": "ValidationError",
  "message": "Human-readable error description",
  "detail": {
    "field": "port",
    "value": 99999,
    "reason": "Port must be between 1 and 65535"
  }
}
```

### Server Error (5xx)

```json
{
  "error": true,
  "error_type": "InternalError",
  "message": "An unexpected error occurred",
  "detail": {
    "trace_id": "abc-123-def-456"
  }
}
```

---

## Status Codes

### Success Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Successful GET request, successful probe (service reachable) |
| 201 | Created | Successful upload or service creation |

### Client Error Codes

| Code | Meaning | When |
|------|---------|------|
| 400 | Bad Request | Invalid parameters, missing required fields, invalid values |
| 404 | Not Found | Unknown `rootfs_id` or `service_id` |
| 409 | Conflict | Port already in use, service already stopped |
| 413 | Payload Too Large | Firmware/rootfs exceeds size limit |
| 415 | Unsupported Media Type | Wrong Content-Type header |
| 422 | Unprocessable Entity | Valid JSON but semantically invalid request |

### Server Error Codes

| Code | Meaning | When |
|------|---------|------|
| 500 | Internal Server Error | Unexpected error, QEMU binary not found |
| 503 | Service Unavailable | Port range exhausted, storage full |
| 504 | Gateway Timeout | Probe timeout, exec timeout |

---

## Rate Limiting

The Emulation Agent does not currently implement built-in rate limiting. It is designed for single-user or small-team use on an internal network.

**Recommendations for high-load scenarios:**

1. Use a reverse proxy (nginx, haproxy) in front of the agent to add rate limiting
2. Limit concurrent emulations to system capacity (typically 10-20)
3. Implement client-side backoff for probe and exec operations

**Example nginx rate-limiting configuration:**

```nginx
limit_req_zone $binary_remote_addr zone=emu_limit:10m rate=30r/m;

server {
    listen 9100;
    location /api/ {
        limit_req zone=emu_limit burst=10 nodelay;
        proxy_pass http://127.0.0.1:9000;
    }
}
```

---

## Versioning Policy

### Current Version

The API is currently at version 1.0.0 (no explicit version prefix).

### Versioning Approach

The API does not currently include a version prefix in the URL (e.g., `/v1/api/...`). When versioned, the plan is:

1. All endpoints will be prefixed with `/v1/` (e.g., `/v1/api/health`)
2. New major versions will use `/v2/`, `/v3/`, etc.
3. Old versions will be maintained for a deprecation period of 6 months
4. Deprecation notices will be returned via the `Warning` HTTP header

### Compatibility

Backward-incompatible changes require a new API version. Backward-compatible changes include:
- Adding new optional fields to request bodies
- Adding new fields to response bodies
- Adding new endpoints
- Adding new query parameters with defaults

### Deprecation Timeline

| Phase | Duration | Behavior |
|-------|----------|----------|
| Active | - | Full support |
| Deprecated | 3 months | `Warning` header on responses; still fully functional |
| Sunset | 3 months | Returns `410 Gone` with migration instructions |
| Removed | - | Endpoint removed |

---

## Complete API Workflow Example

Below is a complete end-to-end workflow using all API endpoints:

```bash
#!/bin/bash
# Complete API workflow: firmware to analysis
# Assumes Emulation Agent running on localhost:9100

AGENT="http://localhost:9100"

echo "=== Step 1: Health Check ==="
curl -s $AGENT/api/health | jq '.status'

echo ""
echo "=== Step 2: Upload Rootfs ==="
ROOTFS_RESPONSE=$(curl -s -X POST $AGENT/api/upload_rootfs \
  -F "file=@squashfs-root.tar.gz")
echo "$ROOTFS_RESPONSE" | jq '.'
ROOTFS_ID=$(echo "$ROOTFS_RESPONSE" | jq -r '.rootfs_id')
ARCH=$(echo "$ROOTFS_RESPONSE" | jq -r '.arch')
echo "Rootfs: $ROOTFS_ID ($ARCH)"

echo ""
echo "=== Step 3: Check Architecture Info ==="
curl -s $AGENT/api/architectures | jq --arg arch "$ARCH" \
  '.architectures[] | select(.name == $arch)'

echo ""
echo "=== Step 4: Configure NVRAM ==="
curl -s -X POST $AGENT/api/nvram_config \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"config\": {
      \"lan_ipaddr\": \"192.168.0.1\",
      \"http_port\": \"80\",
      \"product_name\": \"TestDevice\"
    }
  }" | jq '.'

echo ""
echo "=== Step 5: Explore Binaries ==="
curl -s -X POST $AGENT/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"ls /usr/sbin/ /bin/ 2>/dev/null | head -20\",
    \"timeout\": 10
  }" | jq '.stdout'

echo ""
echo "=== Step 6: Start Service ==="
SERVICE_RESPONSE=$(curl -s -X POST $AGENT/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [\"-p\", \"8880\"],
    \"port\": 8880
  }")
echo "$SERVICE_RESPONSE" | jq '.'
SERVICE_ID=$(echo "$SERVICE_RESPONSE" | jq -r '.service_id')

echo ""
echo "=== Step 7: Wait and Probe ==="
sleep 3
curl -s -X POST $AGENT/api/probe \
  -H "Content-Type: application/json" \
  -d '{
    "host": "127.0.0.1",
    "port": 8880,
    "protocol": "http",
    "http_path": "/"
  }' | jq '.'

echo ""
echo "=== Step 8: Check Service Status ==="
curl -s $AGENT/api/service/$SERVICE_ID | jq '.status, .uptime_seconds, .stdout_tail'

echo ""
echo "=== Step 9: List All Services ==="
curl -s $AGENT/api/services | jq '.total_running, .services[].binary_name'

echo ""
echo "=== Step 10: Execute Analysis Command ==="
curl -s -X POST $AGENT/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"file /usr/sbin/httpd && echo '---' && strings /usr/sbin/httpd | grep -E '(nvram|system|exec|popen)' | head -10\",
    \"timeout\": 10
  }" | jq '.stdout'

echo ""
echo "=== Step 11: Cleanup ==="
curl -s -X POST $AGENT/api/stop_service/$SERVICE_ID | jq '.status'
echo "Done."
```

---

## Python Client Example

```python
#!/usr/bin/env python3
"""Example Python client for the Emulation Agent API."""

import requests
import time
import json
from typing import Optional, Dict, Any


class EmulationAgentClient:
    """Client for the Emulation Agent REST API."""

    def __init__(self, base_url: str = "http://localhost:9100"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "EmulationAgentClient/1.0"})

    def health(self) -> Dict[str, Any]:
        """Check server health."""
        r = self.session.get(f"{self.base_url}/api/health")
        r.raise_for_status()
        return r.json()

    def upload_rootfs(self, file_path: str) -> Dict[str, Any]:
        """Upload a root filesystem archive."""
        with open(file_path, "rb") as f:
            r = self.session.post(
                f"{self.base_url}/api/upload_rootfs",
                files={"file": f}
            )
        r.raise_for_status()
        return r.json()

    def start_service(
        self,
        rootfs_id: str,
        binary_path: str,
        port: Optional[int] = None,
        args: Optional[list] = None,
        strace: bool = False
    ) -> Dict[str, Any]:
        """Start a service in QEMU emulation."""
        payload = {
            "rootfs_id": rootfs_id,
            "binary_path": binary_path,
            "args": args or [],
            "strace": strace,
        }
        if port is not None:
            payload["port"] = port
        r = self.session.post(
            f"{self.base_url}/api/start_service",
            json=payload
        )
        r.raise_for_status()
        return r.json()

    def get_service(self, service_id: str) -> Dict[str, Any]:
        """Get service status."""
        r = self.session.get(f"{self.base_url}/api/service/{service_id}")
        r.raise_for_status()
        return r.json()

    def stop_service(self, service_id: str, force: bool = False) -> Dict[str, Any]:
        """Stop a service."""
        r = self.session.post(
            f"{self.base_url}/api/stop_service/{service_id}",
            params={"force": force}
        )
        r.raise_for_status()
        return r.json()

    def list_services(self, status: Optional[str] = None) -> Dict[str, Any]:
        """List all services."""
        params = {}
        if status:
            params["status"] = status
        r = self.session.get(f"{self.base_url}/api/services", params=params)
        r.raise_for_status()
        return r.json()

    def probe(
        self, host: str, port: int, protocol: str = "tcp",
        http_path: str = "/", timeout: int = 10
    ) -> Dict[str, Any]:
        """Probe a service."""
        payload = {
            "host": host,
            "port": port,
            "protocol": protocol,
        }
        if protocol == "http":
            payload["http_path"] = http_path
        if timeout:
            payload["timeout"] = timeout
        r = self.session.post(f"{self.base_url}/api/probe", json=payload)
        r.raise_for_status()
        return r.json()

    def exec_command(
        self, rootfs_id: str, command: str, timeout: int = 30
    ) -> Dict[str, Any]:
        """Execute a command in the emulated environment."""
        r = self.session.post(
            f"{self.base_url}/api/exec",
            json={
                "rootfs_id": rootfs_id,
                "command": command,
                "timeout": timeout
            }
        )
        r.raise_for_status()
        return r.json()

    def nvram_config(
        self, rootfs_id: str, config: Dict[str, str], merge: bool = True
    ) -> Dict[str, Any]:
        """Configure NVRAM settings."""
        r = self.session.post(
            f"{self.base_url}/api/nvram_config",
            json={
                "rootfs_id": rootfs_id,
                "config": config,
                "merge": merge
            }
        )
        r.raise_for_status()
        return r.json()

    def architectures(self) -> Dict[str, Any]:
        """Get supported architectures."""
        r = self.session.get(f"{self.base_url}/api/architectures")
        r.raise_for_status()
        return r.json()


# Example usage
if __name__ == "__main__":
    client = EmulationAgentClient("http://localhost:9100")

    # Health check
    health = client.health()
    print(f"Server status: {health['status']}")
    print(f"Available QEMU: {json.dumps(health['qemu_binaries'], indent=2)}")

    # Upload rootfs
    result = client.upload_rootfs("squashfs-root.tar.gz")
    rootfs_id = result["rootfs_id"]
    print(f"Uploaded rootfs: {rootfs_id} ({result['arch']})")

    # Configure NVRAM
    client.nvram_config(rootfs_id, {
        "lan_ipaddr": "192.168.0.1",
        "http_port": "80",
        "product_name": "TestDevice"
    })

    # Start httpd
    service = client.start_service(rootfs_id, "/usr/sbin/httpd", port=8080)
    service_id = service["service_id"]
    print(f"Started service: {service_id} (PID: {service['pid']})")

    # Wait and probe
    time.sleep(3)
    probe_result = client.probe("127.0.0.1", 8080, "http")
    print(f"Probe result: reachable={probe_result['reachable']}, "
          f"status={probe_result.get('http_status')}")

    # Execute command
    exec_result = client.exec_command(rootfs_id, "file /usr/sbin/httpd")
    print(f"Command output: {exec_result['stdout'][:200]}")

    # Cleanup
    client.stop_service(service_id)
    print("Service stopped.")
```
