# Emulation Agent Architecture

> Deep architecture documentation for the Emulation Agent firmware emulation service.
>
> 固件模拟服务的深度架构文档

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [System Overview](#system-overview)
3. [Component Architecture](#component-architecture)
4. [Data Flow](#data-flow)
5. [Process Lifecycle Management](#process-lifecycle-management)
6. [Port Allocation Strategy](#port-allocation-strategy)
7. [Concurrency Model](#concurrency-model)
8. [Error Handling Strategy](#error-handling-strategy)
9. [Security Considerations](#security-considerations)
10. [Performance Characteristics](#performance-characteristics)
11. [Extension Points](#extension-points)

---

## Design Philosophy

### Core Principles

1. **Simplicity over completeness**: The Emulation Agent is not a full system emulator (like QEMU system-mode or Firmadyne). It focuses on user-mode QEMU for individual binary emulation. This dramatically reduces complexity while covering the vast majority of vulnerability research use cases.

2. **API-first design**: Every capability is exposed through a clean REST API. This enables integration with any tool or language, and supports the primary use case of being a backend for automated vulnerability research pipelines (vulnagent).

3. **Stateless with explicit state**: The server itself is stateless -- all state is stored on disk (rootfs files, NVRAM configs, process PIDs). This means the server can be restarted without losing data, and multiple server instances can share storage.

4. **Fail gracefully**: When emulation fails (which it often does with embedded firmware), the system provides clear error messages, partial results, and actionable debugging information rather than silent failures.

5. **Security by isolation**: Each emulated service runs in its own process group with resource limits. The API does not expose dangerous operations (like raw filesystem write) without explicit configuration.

### Design Trade-offs

| Decision | Alternative | Rationale |
|----------|------------|-----------|
| User-mode QEMU only | System-mode QEMU | 90% of vuln research needs only user-mode; system-mode adds enormous complexity |
| File-based state | In-memory state | Survives server restarts; easier debugging |
| Synchronous QEMU processes | Async subprocess | QEMU user-mode is inherently synchronous; async wrappers add complexity without benefit |
| Single-node architecture | Distributed | Target use case (single researcher workstation) does not need distribution |
| No authentication | API keys / OAuth | Internal network only; SSH tunnel provides transport security |

---

## System Overview

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        EMULATION AGENT SYSTEM                         │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                      API LAYER                               │     │
│  │  ┌──────────┐  ┌───────────┐  ┌────────┐  ┌─────────────┐  │     │
│  │  │  Health  │  │  Upload   │  │ Service│  │    Probe     │  │     │
│  │  │  Router  │  │  Router   │  │ Router │  │   Router     │  │     │
│  │  └──────────┘  └───────────┘  └────────┘  └─────────────┘  │     │
│  │  ┌──────────┐  ┌───────────┐  ┌──────────────────────────┐ │     │
│  │  │  Exec    │  │   NVRAM   │  │   Architecture Info      │ │     │
│  │  │  Router  │  │   Router  │  │   Router                 │ │     │
│  │  └──────────┘  └───────────┘  └──────────────────────────┘ │     │
│  └────────────────────────────────────────────────────────────┘     │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────┐     │
│  │                    SERVICE LAYER                              │     │
│  │  ┌───────────┐  ┌─────────────┐  ┌──────────────────────┐   │     │
│  │  │ Extractor │  │  Emulator   │  │  Process Manager     │   │     │
│  │  │ Service   │  │  Service    │  │                      │   │     │
│  │  │           │  │             │  │  ┌────────────────┐  │   │     │
│  │  │ - binwalk │  │ - QEMU mgmt │  │  │ Process Pool   │  │   │     │
│  │  │ - tar     │  │ - ELF det.  │  │  │ (pid→service)  │  │   │     │
│  │  │ - arch    │  │ - env setup │  │  │ (port→service) │  │   │     │
│  │  └───────────┘  └─────────────┘  │  └────────────────┘  │   │     │
│  │                                  └──────────────────────┘   │     │
│  │  ┌───────────┐  ┌─────────────┐  ┌──────────────────────┐   │     │
│  │  │  Prober   │  │   NVRAM     │  │   Port Manager       │   │     │
│  │  │  Service  │  │   Manager   │  │                      │   │     │
│  │  │           │  │             │  │  - Allocate port      │   │     │
│  │  │ - TCP     │  │ - Template  │  │  - Release port      │   │     │
│  │  │ - HTTP    │  │ - Custom    │  │  - Detect conflicts   │   │     │
│  │  │ - Telnet  │  │ - Persist   │  │                      │   │     │
│  │  └───────────┘  └─────────────┘  └──────────────────────┘   │     │
│  └────────────────────────────────────────────────────────────┘     │
│                              │                                       │
│  ┌───────────────────────────▼─────────────────────────────────┐     │
│  │                  INFRASTRUCTURE LAYER                         │     │
│  │  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐  │     │
│  │  │  Filesystem  │  │  QEMU Binaries │  │  Network Stack  │  │     │
│  │  │  ./rootfs/   │  │  qemu-*-static │  │  TCP/UDP ports  │  │     │
│  │  │  ./nvram/    │  │                │  │  localhost only │  │     │
│  │  └──────────────┘  └────────────────┘  └─────────────────┘  │     │
│  └────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Inventory

| Component | Responsibility | Key Technologies |
|-----------|---------------|-----------------|
| **FastAPI Server** | HTTP API, request routing, validation | FastAPI, Pydantic, Uvicorn |
| **Extractor Service** | Firmware extraction, format detection | binwalk, tar, file |
| **Architecture Detector** | ELF header analysis, arch determination | readelf, ELF parsing |
| **Emulator Service** | QEMU process spawning, env setup | QEMU user-mode, subprocess |
| **Process Manager** | Process lifecycle tracking, health monitoring | psutil, os.kill, PID management |
| **Port Manager** | Dynamic port allocation, conflict detection | socket, port range tracking |
| **Prober Service** | TCP/HTTP/Telnet connectivity testing | socket, httpx, telnetlib |
| **NVRAM Manager** | NVRAM configuration file management | file I/O, template system |
| **CLI Tool** | Command-line interface for local management | argparse, rich |

---

## Component Interaction Diagrams

### Service Startup Sequence

```
Client                    FastAPI                  Process Mgr          Emulator            QEMU
  │                         │                         │                    │                  │
  │  POST /start_service    │                         │                    │                  │
  │────────────────────────>│                         │                    │                  │
  │                         │                         │                    │                  │
  │                         │  Validate request       │                    │                  │
  │                         │  (rootfs_id exists?)    │                    │                  │
  │                         │──────────┐              │                    │                  │
  │                         │<─────────┘              │                    │                  │
  │                         │                         │                    │                  │
  │                         │  Detect architecture    │                    │                  │
  │                         │  (from rootfs metadata) │                    │                  │
  │                         │──────────┐              │                    │                  │
  │                         │<─────────┘              │                    │                  │
  │                         │                         │                    │                  │
  │                         │  Allocate port          │                    │                  │
  │                         │  (if not specified)     │                    │                  │
  │                         │──────────┐              │                    │                  │
  │                         │<─────────┘              │                    │                  │
  │                         │                         │                    │                  │
  │                         │  Start service          │                    │                  │
  │                         │────────────────────────>│                    │                  │
  │                         │                         │                    │                  │
  │                         │                         │  spawn QEMU        │                  │
  │                         │                         │───────────────────>│                  │
  │                         │                         │                    │  qemu-mipsel     │
  │                         │                         │                    │  -L /rootfs      │
  │                         │                         │                    │  /bin/httpd      │
  │                         │                         │                    │  -p 8080        │
  │                         │                         │                    │─────────────────>│
  │                         │                         │                    │                  │
  │                         │                         │  Register PID      │                  │
  │                         │                         │<───────────────────│                  │
  │                         │                         │                    │                  │
  │                         │                         │  Health check      │                  │
  │                         │                         │  (wait 2s, check   │                  │
  │                         │                         │   process alive)   │                  │
  │                         │                         │──────────┐         │                  │
  │                         │                         │<─────────┘         │                  │
  │                         │                         │                    │                  │
  │                         │  Return service_id,     │                    │                  │
  │                         │  pid, port, status      │                    │                  │
  │<────────────────────────│                         │                    │                  │
  │                         │                         │                    │                  │
```

### Probe Sequence

```
Client                    FastAPI                  Prober               Emulated Service
  │                         │                         │                       │
  │  POST /probe            │                         │                       │
  │────────────────────────>│                         │                       │
  │                         │                         │                       │
  │                         │  Validate request       │                       │
  │                         │──────────┐              │                       │
  │                         │<─────────┘              │                       │
  │                         │                         │                       │
  │                         │  Determine protocol     │                       │
  │                         │  (tcp/http/telnet)      │                       │
  │                         │                         │                       │
  │                         │  Probe                  │                       │
  │                         │────────────────────────>│                       │
  │                         │                         │                       │
  │                         │              ┌─TCP──────│  connect(host, port)  │
  │                         │              │          │──────────────────────>│
  │                         │              │          │<──────────────────────│
  │                         │              │          │   SYN-ACK             │
  │                         │              │          │                       │
  │                         │              ├─HTTP─────│  GET / HTTP/1.1       │
  │                         │              │          │──────────────────────>│
  │                         │              │          │<──────────────────────│
  │                         │              │          │   HTTP 200 OK         │
  │                         │              │          │                       │
  │                         │              └─TELNET───│  connect, read banner │
  │                         │                         │──────────────────────>│
  │                         │                         │<──────────────────────│
  │                         │                         │   login:              │
  │                         │                         │                       │
  │                         │  Return probe result    │                       │
  │<────────────────────────│                         │                       │
  │                         │                         │                       │
```

---

## Data Flow

### Flow 1: Firmware Upload to Rootfs Storage

```
                          ┌─────────────────────────────────────────┐
                          │           /api/upload_rootfs             │
                          │                                         │
 Firmware                  │  1. Receive multipart file upload       │
 .tar.gz ─────────────────>│     (max size: EMU_MAX_ROOTFS_SIZE_MB)  │
                          │                                         │
                          │  2. Generate rootfs_id (uuid4)           │
                          │     - Create ./rootfs/<rootfs_id>/       │
                          │                                         │
                          │  3. Extract archive                      │
                          │     - tar -xzf <file> -C ./rootfs/<id>/  │
                          │     - Validate directory structure       │
                          │                                         │
                          │  4. Detect architecture                  │
                          │     - find ./rootfs/<id>/ -type f -exec  │
                          │     - file {} \; | grep ELF              │
                          │     - readelf -h on each ELF binary      │
                          │     - Select majority architecture       │
                          │                                         │
                          │  5. Store metadata                       │
                          │     - ./rootfs/<id>/metadata.json        │
                          │       {arch, timestamp, file_count, ...} │
                          │                                         │
                          │  6. Return response                      │
                          └─────────────┬───────────────────────────┘
                                        │
                                        v
                          {"rootfs_id": "abc123", "arch": "mipsel",
                           "file_count": 1543, "status": "ok"}
```

### Flow 2: Service Start to QEMU Process

```
                          ┌─────────────────────────────────────────┐
                          │           /api/start_service             │
                          │                                         │
 Service Request          │  1. Validate parameters                  │
 (rootfs_id, binary,      │     - rootfs_id exists?                  │
  args, port) ───────────>│     - binary_path exists in rootfs?      │
                          │     - port available?                    │
                          │                                         │
                          │  2. Resolve architecture                 │
                          │     - Read ./rootfs/<id>/metadata.json   │
                          │     - Map arch → QEMU binary path        │
                          │                                         │
                          │  3. Allocate port (if auto)              │
                          │     - PortManager.allocate()             │
                          │                                         │
                          │  4. Build QEMU command                   │
                          │     qemu-mipsel-static \                 │
                          │       -L ./rootfs/<id>/ \                │
                          │       -E LD_LIBRARY_PATH=/lib:/usr/lib \ │
                          │       ./rootfs/<id>/usr/sbin/httpd \     │
                          │       -p 8080                            │
                          │                                         │
                          │  5. Spawn process                        │
                          │     - subprocess.Popen(cmd,              │
                          │         stdout=PIPE, stderr=PIPE,        │
                          │         preexec_fn=os.setsid)            │
                          │     - Generate service_id                │
                          │                                         │
                          │  6. Health check                         │
                          │     - Wait 2 seconds                     │
                          │     - Check process.poll() is None       │
                          │     - Read initial stdout/stderr         │
                          │                                         │
                          │  7. Register with ProcessManager         │
                          │     - Track: pid, port, rootfs_id, etc.  │
                          │                                         │
                          │  8. Return response                      │
                          └─────────────┬───────────────────────────┘
                                        │
                                        v
                          {"service_id": "svc-7a3f", "pid": 45821,
                           "port": 8080, "status": "running"}
```

### Flow 3: Probe Connection

```
                          ┌─────────────────────────────────────────┐
                          │              /api/probe                  │
                          │                                         │
 Probe Request            │  1. Validate parameters                  │
 (host, port, protocol)   │     - host reachable? (127.0.0.1)        │
 ────────────────────────>│     - port in valid range?               │
                          │     - protocol supported?                │
                          │                                         │
                          │  2. Protocol dispatch                    │
                          │     ├── tcp: socket.connect() timeout=5s │
                          │     ├── http: httpx.get(f"http://{h}:{p}│
                          │     │         {path}", timeout=10s)      │
                          │     └── telnet: connect, read until      │
                          │               banner or 5s timeout       │
                          │                                         │
                          │  3. Collect response data                │
                          │     - TCP: latency_ms                    │
                          │     - HTTP: status, headers, body_preview│
                          │     - Telnet: banner_text                │
                          │                                         │
                          │  4. Return response                      │
                          └─────────────┬───────────────────────────┘
                                        │
                                        v
                          {"reachable": true, "http_status": 200,
                           "http_headers": {...},
                           "http_body_preview": "<html>..."}
```

### Flow 4: Exec Command in Emulated Environment

```
                          ┌─────────────────────────────────────────┐
                          │              /api/exec                   │
                          │                                         │
 Exec Request             │  1. Validate parameters                  │
 (rootfs_id, command,     │     - rootfs_id exists?                  │
  timeout) ──────────────>│     - command is not empty?              │
                          │     - timeout within limits?              │
                          │                                         │
                          │  2. Resolve busybox/shell                │
                          │     - Find busybox in rootfs             │
                          │     - Fall back to /bin/sh               │
                          │                                         │
                          │  3. Build QEMU command                   │
                          │     qemu-mipsel-static \                 │
                          │       -L ./rootfs/<id>/ \                │
                          │       ./rootfs/<id>/bin/busybox \        │
                          │       sh -c "ls /usr/sbin/"              │
                          │                                         │
                          │  4. Execute with timeout                 │
                          │     - subprocess.run(cmd, timeout=N)     │
                          │     - Capture stdout, stderr             │
                          │                                         │
                          │  5. Return response                      │
                          │     - exit_code, stdout, stderr          │
                          └─────────────┬───────────────────────────┘
                                        │
                                        v
                          {"exit_code": 0,
                           "stdout": "httpd\ntelnetd\n...",
                           "stderr": ""}
```

---

## Process Lifecycle Management

### State Machine

```
                    ┌─────────┐
                    │  NONE   │
                    └────┬────┘
                         │ start_service()
                         v
                    ┌─────────┐
              ┌────>│ STARTING│────┐
              │     └─────────┘    │
              │ (health check)     │ (process died)
              │                    v
              │               ┌─────────┐
              │               │ CRASHED │──> (auto-cleanup after TTL)
              │               └─────────┘
              v
         ┌─────────┐
         │ RUNNING │──── stop_service() ────>┌──────────┐
         └────┬────┘                         │ STOPPING │
              │                              └────┬─────┘
              │ (process exits                    │
              │  unexpectedly)                    v
              v                              ┌─────────┐
         ┌─────────┐                         │ STOPPED │
         │  DEAD   │                         └─────────┘
         └─────────┘
```

### Process Tracking

Each emulated service is tracked with the following metadata:

```python
{
    "service_id": "svc-7a3f",       # Unique identifier
    "rootfs_id": "abc123",          # Parent rootfs
    "binary_path": "/usr/sbin/httpd",
    "binary_name": "httpd",
    "args": ["-p", "8080"],
    "port": 8080,                   # Network port
    "pid": 45821,                   # OS process ID
    "pgid": 45821,                  # Process group ID (for cleanup)
    "status": "running",            # Current state
    "started_at": "2026-07-02T10:30:00Z",
    "qemu_binary": "qemu-mipsel-static",
    "arch": "mipsel",
    "stdout_buffer": "",            # Captured stdout (ring buffer, last 64KB)
    "stderr_buffer": "",            # Captured stderr (ring buffer, last 64KB)
    "exit_code": None,              # Exit code when process ends
    "env_vars": {},                 # Environment variables
    "strace_enabled": False         # strace capture flag
}
```

### Cleanup Strategy

1. **Graceful shutdown** (`stop_service`): Send SIGTERM, wait 5s, then SIGKILL process group
2. **Orphan detection**: Background thread polls all running processes every 30s; dead processes are moved to STOPPED state
3. **Port release**: On stop/crash, port is immediately returned to the available pool
4. **Resource limits**: Each QEMU process is limited to 1GB RAM (via `prlimit` or cgroups if available)
5. **TTL for rootfs**: Uploaded rootfs can be configured with a TTL (time-to-live), after which they are cleaned up

---

## Port Allocation Strategy

### Algorithm

```
Port Range: EMU_PORT_RANGE_START (8000) to EMU_PORT_RANGE_END (9000)

Allocation:
  1. If client specifies a port:
     - Check if port is in use (by any running service)
     - If free, assign it
     - If in use, return 409 Conflict with list of free ports
  2. If client does not specify a port (auto-allocate):
     - Iterate from START to END
     - First free port wins
     - No port wrapping (return error if range exhausted)

Deallocation:
  - On service stop/crash: port returned to pool immediately
  - No grace period (port is available for immediate reuse)

Bookkeeping:
  - In-memory set of used ports: {8080, 8081, 9000}
  - Port→service_id mapping for conflict resolution
  - No persistence across server restarts (ports are OS-level, not agent-level)
```

### Port Conflict Resolution

```python
def allocate_port(requested_port: Optional[int] = None) -> int:
    if requested_port is not None:
        if requested_port in used_ports:
            raise PortConflictError(
                f"Port {requested_port} is in use by service {port_to_service[requested_port]}"
            )
        if is_port_bound_os(requested_port):
            raise PortConflictError(
                f"Port {requested_port} is in use by another process"
            )
        used_ports.add(requested_port)
        return requested_port

    # Auto-allocate
    for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if port not in used_ports and not is_port_bound_os(port):
            used_ports.add(port)
            return port

    raise PortExhaustionError("No free ports in range")
```

---

## Concurrency Model

### Async FastAPI + Synchronous QEMU

The Emulation Agent uses a pragmatic hybrid concurrency model:

```
                    FastAPI Async Event Loop
                    ┌──────────────────────────────────────┐
                    │                                      │
  Incoming          │  ┌────────┐    ┌────────┐           │
  HTTP Requests ────┼─>│ Route  │    │ Route  │           │
                    │  │ Handler│    │ Handler│           │
                    │  │ (async)│    │ (async)│           │
                    │  └───┬────┘    └───┬────┘           │
                    │      │              │                │
                    │      │ run_in_executor()             │
                    │      v              v                │
                    │  ┌──────────────────────────────┐   │
                    │  │    ThreadPoolExecutor         │   │
                    │  │    (default: 4 workers)       │   │
                    │  │                              │   │
                    │  │  ┌────────┐  ┌────────┐      │   │
                    │  │  │ Worker │  │ Worker │ ...  │   │
                    │  │  │ Thread │  │ Thread │      │   │
                    │  │  └───┬────┘  └───┬────┘      │   │
                    │  │      │            │           │   │
                    │  │      v            v           │   │
                    │  │  ┌────────────────────────┐  │   │
                    │  │  │  subprocess.Popen()    │  │   │
                    │  │  │  (blocking call)       │  │   │
                    │  │  └────────────────────────┘  │   │
                    │  └──────────────────────────────┘   │
                    │                                      │
                    │  ┌──────────────────────────────┐   │
                    │  │   Background Tasks            │   │
                    │  │   - Health monitor (30s)      │   │
                    │  │   - Rootfs cleanup (hourly)   │   │
                    │  │   - Log rotation (daily)      │   │
                    │  └──────────────────────────────┘   │
                    └──────────────────────────────────────┘
```

### Why This Model?

- **FastAPI is async**: The HTTP layer benefits from async I/O for handling many concurrent connections
- **QEMU is blocking**: QEMU user-mode is inherently synchronous -- there is no async QEMU API
- **ThreadPoolExecutor**: Bridges the gap by running blocking QEMU operations in thread workers
- **Process-level isolation**: QEMU processes are OS-level processes, completely isolated from the Python runtime
- **No GIL contention**: QEMU execution happens outside the Python interpreter, so the GIL does not block other requests

### Scaling Characteristics

- **Concurrent uploads**: Async I/O handles multiple file uploads efficiently
- **Concurrent emulations**: Limited by system resources (RAM, CPU cores) and port range
- **Typical capacity**: 10-20 concurrent emulated services on a 16GB VM
- **Bottleneck**: Memory bandwidth for large rootfs operations, not CPU

---

## Error Handling Strategy

### Error Categories

| Category | HTTP Status | Example | Recovery |
|----------|------------|---------|----------|
| **Validation** | 400 | Missing required field, invalid arch | Fix request and retry |
| **Not Found** | 404 | Unknown rootfs_id, unknown service_id | Upload rootfs first |
| **Conflict** | 409 | Port already in use | Use different port |
| **Server Error** | 500 | QEMU binary not found, disk full | Fix server configuration |
| **Timeout** | 504 | Exec command exceeded timeout | Increase timeout or simplify command |

### Error Response Format

```json
{
  "error": true,
  "error_type": "PortConflictError",
  "message": "Port 8080 is in use by service svc-7a3f (httpd)",
  "detail": {
    "requested_port": 8080,
    "conflicting_service": "svc-7a3f",
    "conflicting_binary": "httpd",
    "available_ports": [8081, 8082, 8083]
  }
}
```

### QEMU Failure Handling

When a QEMU process crashes:

1. The ProcessManager detects the crash via `process.poll()` returning non-None
2. The exit code and stderr buffer are captured
3. The service status is updated to CRASHED
4. Common failure patterns are analyzed:
   - `SIGSEGV (11)`: Binary crashed, possibly missing library or bad NVRAM
   - `SIGILL (4)`: Wrong architecture QEMU binary
   - `exit(1)` with "cannot execute": Missing interpreter or permissions
   - `exit(127)`: Binary not found in rootfs
5. A human-readable diagnosis is included in the service status

```json
{
  "service_id": "svc-7a3f",
  "status": "crashed",
  "exit_code": -11,
  "diagnosis": "SIGSEGV: The binary crashed with a segmentation fault. "
               "This is common when NVRAM configuration is missing or "
               "required libraries are not available. Try: "
               "1. Configure NVRAM via /api/nvram_config "
               "2. Check library dependencies with 'ldd /usr/sbin/httpd' "
               "via /api/exec",
  "stderr_tail": "Error loading shared library libnvram.so: No such file"
}
```

---

## Security Considerations

### Threat Model

The Emulation Agent is designed to run on an **internal network only** (not exposed to the public internet). Its threat model assumes:

- **Trusted API clients**: vulnagent or researcher tools on the same network
- **Untrusted firmware images**: The firmware being analyzed may be malicious
- **No multi-tenancy**: Single user/researcher per instance

### Mitigations

#### QEMU Process Isolation

```
┌────────────────────────────────────────────┐
│              Host System                    │
│  ┌──────────────────────────────────────┐  │
│  │       Emulation Agent (Python)       │  │
│  │                                      │  │
│  │  ┌────────────────────────────────┐  │  │
│  │  │  Process Group (pgid=45821)    │  │  │
│  │  │  ┌──────────────────────────┐  │  │  │
│  │  │  │  qemu-mipsel-static      │  │  │  │
│  │  │  │  (user-mode, no root)    │  │  │  │
│  │  │  │                          │  │  │  │
│  │  │  │  Limits:                 │  │  │  │
│  │  │  │  - RLIMIT_AS: 1GB       │  │  │  │
│  │  │  │  - RLIMIT_CPU: 3600s    │  │  │  │
│  │  │  │  - RLIMIT_NPROC: 50     │  │  │  │
│  │  │  │  - RLIMIT_NOFILE: 1024  │  │  │  │
│  │  │  │  - No network namespace  │  │  │  │
│  │  │  │    (needs localhost bind)│  │  │  │
│  │  │  └──────────────────────────┘  │  │  │
│  │  └────────────────────────────────┘  │  │
│  └──────────────────────────────────────┘  │
└────────────────────────────────────────────┘
```

#### Resource Limits

```python
def set_process_limits():
    """Apply resource limits to QEMU processes."""
    import resource
    # 1GB virtual memory
    resource.setrlimit(resource.RLIMIT_AS, (1_073_741_824, 1_073_741_824))
    # 1 hour CPU time
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    # Max 50 child processes
    resource.setrlimit(resource.RLIMIT_NPROC, (50, 50))
    # Max 1024 open files
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
```

#### Filesystem Isolation

- QEMU user-mode already provides filesystem translation (the `-L` flag)
- Emulated binaries see the rootfs as `/`, not the host filesystem
- Write operations go to the rootfs copy, not the host
- The `./rootfs/` and `./nvram/` directories are the only writable paths

#### Network Isolation

- Emulated services bind to `127.0.0.1` (localhost) by default
- No external network access from emulated processes
- If external access is needed, SSH tunneling or VPN is recommended
- Port range is limited (8000-9000) to prevent conflicts with system services

#### API Security

- **No built-in authentication**: Intended for internal network use
- **SSH tunnel recommended**: `ssh -L 9100:localhost:9100 user@agent-host`
- **Request size limits**: Maximum upload size enforced via `EMU_MAX_ROOTFS_SIZE_MB`
- **Input validation**: All API inputs validated through Pydantic models
- **No shell injection in exec**: Commands are passed as arguments (not through shell parsing)

#### Firmware Safety

- Binaries from firmware are executed through QEMU user-mode, which translates syscalls
- Malicious firmware binaries cannot escape QEMU user-mode directly
- System-mode QEMU would provide stronger isolation but is not the default
- Consider running the agent in a VM or container (Docker) for additional isolation

---

## Performance Characteristics

### Benchmarks

| Operation | Typical Time | Bottleneck |
|-----------|-------------|------------|
| Upload rootfs (50MB tar.gz) | 3-5 seconds | Disk I/O |
| Extract rootfs | 5-15 seconds | Disk I/O + CPU (decompression) |
| Architecture detection | 2-5 seconds | File scanning (I/O bound) |
| Start QEMU service | 1-3 seconds | Process spawn + binary loading |
| TCP probe | 10-50 ms | Network round-trip |
| HTTP probe | 50-500 ms | Network + service response time |
| Exec simple command | 0.5-2 seconds | QEMU startup overhead |
| Exec complex command | 1-10 seconds | Command execution time |
| Stop service | 0.1-0.5 seconds | SIGTERM delivery |
| NVRAM config | <0.1 second | File write |

### Memory Usage

| Component | Typical Memory |
|-----------|---------------|
| FastAPI server (idle) | 50-100 MB |
| Per uploaded rootfs (metadata) | 1-5 MB |
| Per QEMU process (light service) | 50-200 MB |
| Per QEMU process (heavy service) | 200-500 MB |
| Total for 10 concurrent services | 2-5 GB |

### Optimization Strategies

1. **Rootfs caching**: Keep frequently-used rootfs on SSD storage
2. **QEMU binary preloading**: QEMU static binaries are small and cached by the OS
3. **Port pre-allocation**: For batch operations, pre-allocate a block of ports
4. **Lazy architecture detection**: Cache arch detection results in metadata.json
5. **Parallel extraction**: Use multiprocessing for extracting large firmware archives

---

## Extension Points

### Adding a New Architecture

1. Add the QEMU binary to the system (`qemu-riscv64-static`, etc.)
2. Register the architecture in the architecture mapping:

```python
ARCH_MAP = {
    "mips": "qemu-mips-static",
    "mipsel": "qemu-mipsel-static",
    "arm": "qemu-arm-static",
    "aarch64": "qemu-aarch64-static",
    "i386": "qemu-i386-static",
    "x86_64": "qemu-x86_64-static",
    # NEW:
    "riscv64": "qemu-riscv64-static",
}
```

3. Add ELF machine identifier mapping:

```python
ELF_MACHINE_MAP = {
    0x08: "mips",       # EM_MIPS
    0x28: "arm",        # EM_ARM
    0x3E: "x86_64",     # EM_X86_64
    0xB7: "aarch64",    # EM_AARCH64
    0xF3: "riscv64",    # EM_RISCV (new)
}
```

### Adding a New Probe Protocol

1. Add the protocol string to the ProbeRequest model
2. Implement the probe function in the Prober service:

```python
async def probe_ssh(host: str, port: int) -> dict:
    """Probe an SSH service and return its banner."""
    try:
        sock = socket.create_connection((host, port), timeout=5)
        banner = sock.recv(1024).decode('utf-8', errors='replace')
        sock.close()
        return {"reachable": True, "banner": banner.strip()}
    except Exception as e:
        return {"reachable": False, "error": str(e)}
```

3. Register in the protocol dispatch table:

```python
PROTOCOL_HANDLERS = {
    "tcp": probe_tcp,
    "http": probe_http,
    "telnet": probe_telnet,
    "ssh": probe_ssh,  # NEW
}
```

### Adding a New API Endpoint

1. Define the Pydantic model
2. Add the route to the FastAPI app
3. Implement the handler:

```python
class SnapshotRequest(BaseModel):
    rootfs_id: str
    snapshot_name: str = "default"

@app.post("/api/snapshot")
async def create_snapshot(req: SnapshotRequest):
    """Create a filesystem snapshot of the rootfs."""
    rootfs_path = get_rootfs_path(req.rootfs_id)
    snapshot_path = f"{rootfs_path}.snapshot.{req.snapshot_name}"
    shutil.copytree(rootfs_path, snapshot_path)
    return {"status": "ok", "snapshot_path": snapshot_path}
```

### Integrating with External Vulnerability Scanners

```python
# Example: Integration with a hypothetical vulnerability scanner
class ExternalScannerRequest(BaseModel):
    service_id: str
    scanner: str  # "nuclei", "zap", "burp"
    config: dict = {}

@app.post("/api/scan")
async def run_external_scan(req: ExternalScannerRequest):
    service = process_manager.get_service(req.service_id)

    if req.scanner == "nuclei":
        result = subprocess.run(
            ["nuclei", "-u", f"http://127.0.0.1:{service.port}", "-json"],
            capture_output=True, text=True, timeout=300
        )
        return {"findings": json.loads(result.stdout)}
    # ... other scanners
```

### Adding Custom Fuzzing Harness

```python
class FuzzRequest(BaseModel):
    service_id: str
    fuzzer: str  # "afl", "libfuzzer", "custom"
    input_dir: str
    timeout: int = 3600

@app.post("/api/fuzz")
async def start_fuzzing(req: FuzzRequest):
    service = process_manager.get_service(req.service_id)

    if req.fuzzer == "afl":
        # AFL++ QEMU mode
        cmd = [
            "afl-fuzz", "-Q",
            "-i", req.input_dir,
            "-o", f"./fuzz_output/{service.service_id}",
            "--", service.binary_path, "@@"
        ]
        proc = subprocess.Popen(cmd)
        return {"fuzz_id": str(uuid.uuid4()), "pid": proc.pid}
```

---

## Configuration Files

### Server Configuration Schema

```yaml
# emulation_agent/config.yaml
server:
  host: "0.0.0.0"
  port: 9100
  workers: 1  # Single worker (state is in-process)

storage:
  rootfs_dir: "./rootfs"
  nvram_dir: "./nvram"
  max_rootfs_size_mb: 2048

qemu:
  search_paths:
    - "/usr/bin"
    - "/usr/local/bin"
  default_timeout: 300
  resource_limits:
    memory_mb: 1024
    cpu_seconds: 3600
    max_processes: 50

ports:
  range_start: 8000
  range_end: 9000

logging:
  level: "info"  # debug | info | warning | error
  format: "json"  # json | text
  file: "/var/log/emulation_agent.log"

cleanup:
  rootfs_ttl_hours: 24  # Auto-clean rootfs after 24h
  check_interval_minutes: 30
```

---

## Monitoring and Observability

### Metrics

- **Request latency**: P50, P95, P99 per endpoint
- **Active services**: Count of running QEMU processes
- **Port utilization**: Used/total port range
- **Disk usage**: Rootfs storage directory size
- **Crash rate**: Services crashing vs. running
- **QEMU startup time**: Time from spawn to health check pass

### Logging Format

```
{"timestamp": "2026-07-02T10:30:00.123Z", "level": "INFO",
 "event": "service_start", "service_id": "svc-7a3f",
 "rootfs_id": "abc123", "binary": "httpd", "arch": "mipsel",
 "port": 8080, "pid": 45821}
```

### Health Endpoint Response

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
    "rootfs_disk_mb": 450,
    "nvram_count": 3
  },
  "ports": {
    "range": "8000-9000",
    "used": 3,
    "available": 997
  },
  "system": {
    "cpu_percent": 12.5,
    "memory_used_mb": 2048,
    "memory_total_mb": 16384,
    "disk_free_gb": 45.2
  }
}
```
