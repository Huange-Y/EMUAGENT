# Emulation Agent User Guide

> Complete user guide for the Emulation Agent firmware emulation service.
>
> 固件模拟服务完整用户指南

---

## Table of Contents

1. [Prerequisites and Installation](#prerequisites-and-installation)
2. [Server Deployment](#server-deployment)
3. [Firmware Preparation](#firmware-preparation)
4. [Emulation Workflows](#emulation-workflows)
5. [Service-Specific Guides](#service-specific-guides)
6. [NVRAM Configuration Guide](#nvram-configuration-guide)
7. [Debugging Emulated Services](#debugging-emulated-services)
8. [Fuzzing Integration](#fuzzing-integration)
9. [Best Practices and Tips](#best-practices-and-tips)

---

## Prerequisites and Installation

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Ubuntu 20.04 / Debian 11 | Ubuntu 22.04+ |
| CPU | 2 cores | 8+ cores |
| RAM | 4 GB | 16+ GB |
| Disk | 20 GB free | 100+ GB SSD |
| Python | 3.10+ | 3.11+ |
| Network | Loopback only | Dedicated VLAN/VM network |

### Installing System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
    qemu-user-static \
    qemu-system-mips \
    qemu-system-arm \
    qemu-system-x86 \
    binwalk \
    python3-pip \
    python3-venv \
    tar \
    gzip \
    file \
    binutils \
    squashfs-tools \
    lzma \
    p7zip-full

# Verify QEMU installation
for qemu_bin in qemu-mips-static qemu-mipsel-static qemu-arm-static qemu-aarch64-static qemu-x86_64-static; do
    if which $qemu_bin > /dev/null 2>&1; then
        echo "OK: $qemu_bin found at $(which $qemu_bin)"
    else
        echo "MISSING: $qemu_bin"
    fi
done
```

### Installing Python Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install
pip install fastapi uvicorn[standard] python-multipart aiofiles httpx psutil

# Verify
python3 -c "import fastapi; print('FastAPI', fastapi.__version__)"
python3 -c "import uvicorn; print('Uvicorn OK')"
```

### Installation Verification

```bash
# Clone the repository
git clone https://github.com/example/emulation_agent.git
cd emulation_agent

# Run the self-check script
python3 -m emulation_agent.cli check
# Expected output:
#   [OK] Python 3.11.2
#   [OK] FastAPI 0.104.1
#   [OK] qemu-mipsel-static
#   [OK] qemu-mips-static
#   [OK] qemu-arm-static
#   [OK] qemu-aarch64-static
#   [OK] qemu-x86_64-static
#   [OK] tar
#   [OK] binwalk
#   [OK] Port range 8000-9000 available
```

---

## Server Deployment

### Local Development Deployment

For personal use on a development workstation:

```bash
cd emulation_agent

# Option 1: Direct uvicorn
uvicorn server:app --host 127.0.0.1 --port 9100 --reload

# Option 2: Via CLI
python3 -m emulation_agent.cli serve --host 127.0.0.1 --port 9100

# Option 3: With gunicorn (production-like)
pip install gunicorn
gunicorn server:app -w 2 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:9100
```

Access the server at `http://127.0.0.1:9100`. Check health:

```bash
curl http://127.0.0.1:9100/api/health
```

### Docker Deployment

Build and run the Docker container:

```bash
# Build
docker build -t emulation-agent:latest .

# Run with volume mounts for persistence
docker run -d \
  --name emulation-agent \
  --restart unless-stopped \
  --network host \
  -v $(pwd)/rootfs:/app/rootfs \
  -v $(pwd)/nvram:/app/nvram \
  -v $(pwd)/config.yaml:/app/config.yaml \
  emulation-agent:latest

# Check logs
docker logs -f emulation-agent

# Access from host
curl http://localhost:9100/api/health
```

Using Docker Compose (with vulnagent integration):

```yaml
# docker-compose.yml
version: '3.8'
services:
  emulation-agent:
    build: .
    ports:
      - "9100:9100"
    volumes:
      - ./rootfs:/app/rootfs
      - ./nvram:/app/nvram
    restart: unless-stopped
    environment:
      - EMU_LOG_LEVEL=info
      - EMU_MAX_ROOTFS_SIZE_MB=4096
    networks:
      - iot-lab

  vulnagent:
    image: vulnagent:latest
    depends_on:
      - emulation-agent
    environment:
      - EMULATION_AGENT_HOST=emulation-agent
      - EMULATION_AGENT_PORT=9100
    networks:
      - iot-lab

networks:
  iot-lab:
    driver: bridge
```

```bash
docker-compose up -d
```

### Remote Ubuntu VM Deployment

This is the primary deployment model when vulnagent runs on Windows and the emulation agent runs on a dedicated Ubuntu VM.

#### Setting Up the Ubuntu VM

```bash
# On a fresh Ubuntu 22.04 VM

# 1. Update system
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install all QEMU packages
sudo apt-get install -y qemu-user-static qemu-system-mips qemu-system-arm qemu-system-x86

# 3. Install Python and tools
sudo apt-get install -y python3-pip python3-venv binwalk file tar gzip

# 4. Create a dedicated user (optional but recommended)
sudo useradd -m -s /bin/bash emuagent
sudo usermod -aG sudo emuagent

# 5. Set up SSH for remote access
# (generate keys, configure authorized_keys, disable password auth)

# 6. Create working directory
sudo mkdir -p /opt/emulation_agent
sudo chown emuagent:emuagent /opt/emulation_agent
```

#### Deploying the Agent to the VM

```bash
# Option A: SCP deployment (from local machine)
scp -P 2222 server.py art@your-vm-ip:/opt/emulation_agent/
scp -r emulation_agent/ art@your-vm-ip:/opt/emulation_agent/

# Option B: Git clone on the VM
ssh -p 2222 art@your-vm-ip
cd /opt/emulation_agent
git clone https://github.com/example/emulation_agent.git .
pip3 install -r requirements.txt

# Option C: One-command deploy script
bash deploy.sh  # deploys to configured VM
```

#### Running as a Systemd Service

Create `/etc/systemd/system/emulation-agent.service`:

```ini
[Unit]
Description=Emulation Agent for Firmware Analysis
After=network.target

[Service]
Type=simple
User=emuagent
WorkingDirectory=/opt/emulation_agent
ExecStart=/opt/emulation_agent/venv/bin/python3 -m uvicorn server:app --host 0.0.0.0 --port 9100
Restart=on-failure
RestartSec=5
Environment="EMU_LOG_LEVEL=info"
Environment="EMU_ROOTFS_DIR=/data/rootfs"
Environment="EMU_NVRAM_DIR=/data/nvram"

# Resource limits
LimitAS=infinity
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable emulation-agent
sudo systemctl start emulation-agent
sudo systemctl status emulation-agent

# Check logs
sudo journalctl -u emulation-agent -f
```

#### SSH Tunnel Setup for Secure Access

When vulnagent runs on a different machine (e.g., Windows), set up an SSH tunnel for secure access:

```bash
# On the Windows machine (or any client)
ssh -f -N -L 9100:localhost:9100 \
  -p 2222 \
  -i ~/.ssh/id_ed25519 \
  art@your-vm-ip

# Now vulnagent can access the agent via localhost:9100
curl http://localhost:9100/api/health
```

For persistent SSH tunnel, use autossh:

```bash
autossh -M 0 -f -N -L 9100:localhost:9100 \
  -p 2222 \
  -i ~/.ssh/id_ed25519 \
  art@your-vm-ip
```

---

## Firmware Preparation

### How to Obtain Firmware

合法获取固件镜像的途径 (Legitimate ways to obtain firmware images):

1. **Vendor websites**: Most router/IoT manufacturers provide firmware downloads on their support pages.
   - D-Link: `https://support.dlink.com`
   - TP-Link: `https://www.tp-link.com/support/download/`
   - Netgear: `https://www.netgear.com/support/download/`
   - Tenda: `https://www.tendacn.com/en/download/`

2. **FCC filings**: IoT devices sold in the US have FCC filings that sometimes include firmware.
   - Search: `https://fccid.io`

3. **Device extraction**: Extract firmware from a physical device you own.
   - UART console access via serial pins
   - JTAG/SWD debugging interface
   - Flash chip dumping (using CH341A programmer or similar)

4. **GPL compliance**: Vendors using Linux/GPL must provide source code, which often includes rootfs.
   - Linksys GPL Center
   - Netgear Open Source Code

5. **Community archives**:
   - OpenWrt package repositories
   - Firmware analysis research datasets

### Extraction Techniques

#### Using binwalk

```bash
# Basic extraction
binwalk -e firmware.bin

# Extraction with full recursion
binwalk -eM firmware.bin

# Specify output directory
binwalk -e --directory=./extracted firmware.bin

# Extract specific file types
binwalk -D 'squashfs:squashfs' firmware.bin
```

Common output structure:

```
extracted/
├── _firmware.bin.extracted/
│   ├── 0.squashfs        # SquashFS filesystem
│   ├── 1.cpio            # CPIO archive
│   └── squashfs-root/     # Extracted root filesystem
│       ├── bin/
│       ├── sbin/
│       ├── lib/
│       ├── usr/
│       ├── etc/
│       └── ...
```

#### Manual Extraction (Advanced)

For firmwares that binwalk cannot handle:

**Identifying the filesystem:**

```bash
# Hex dump to see magic bytes
xxd firmware.bin | head -50

# Search for common filesystem magic bytes
# SquashFS: 68 73 71 73 (hsqs)
# JFFS2:    00 00 19 84
# CramFS:   45 3D CD 28
# YAFFS:    00 00 00 01
binwalk -W firmware.bin

# Use file command
file firmware.bin
```

**Extracting SquashFS manually:**

```bash
# Find the SquashFS offset
binwalk firmware.bin | grep -i squashfs
# DECIMAL       HEXADECIMAL     DESCRIPTION
# 524288        0x80000         Squashfs filesystem, little endian, version 4.0

# Extract with dd
dd if=firmware.bin of=rootfs.squashfs bs=1 skip=524288

# Extract squashfs
unsquashfs rootfs.squashfs
# => squashfs-root/
```

**Extracting JFFS2 manually:**

```bash
# Find JFFS2 offset
binwalk firmware.bin | grep -i jffs2

# Extract
dd if=firmware.bin of=jffs2.bin bs=1 skip=<offset>

# Mount (requires mtd-utils and jffs2 kernel module)
sudo modprobe jffs2
sudo modprobe mtdram total_size=32768 erase_size=256
sudo modprobe mtdblock
sudo dd if=jffs2.bin of=/dev/mtdblock0
sudo mount -t jffs2 /dev/mtdblock0 /mnt/jffs2
```

**Extracting from U-Boot images:**

```bash
# Check for U-Boot header
binwalk firmware.bin | grep -i "u-boot"

# Use mkimage to extract
mkimage -l firmware.bin  # List image info
dd if=firmware.bin of=kernel.bin bs=1 skip=64 count=<length>
```

**Dealing with encrypted/obfuscated firmware:**

```bash
# Common obfuscation: XOR with a single byte
python3 << 'EOF'
key = 0xFF  # Common key; try 0x00-0xFF
with open('firmware.bin', 'rb') as f:
    data = f.read()
with open('firmware.decrypted', 'wb') as f:
    f.write(bytes(b ^ key for b in data))
EOF

# Check entropy to identify encrypted vs compressed regions
binwalk -E firmware.bin
```

### Common Extraction Issues

#### Issue: "No filesystem found"

**Causes:**
- Firmware is encrypted (D-Link uses EnCore, Tenda uses custom encryption)
- Uncommon filesystem type (UBIFS, YAFFS2)
- Firmware image is a raw flash dump without filesystem headers

**Solutions:**
1. Try different binwalk flags: `binwalk -eM --dd='.*' firmware.bin`
2. Analyze entropy with `binwalk -E` to locate filesystem boundaries
3. Search for string references to filesystem types in the binary
4. Try firmware-mod-kit (`firmware-mod-kit/extract-firmware.sh`)

#### Issue: "Extracted but no ELF binaries found"

**Causes:**
- Firmware uses a different format (RTOS, bare-metal)
- Filesystem was not fully extracted
- Binaries are compressed or stripped

**Solutions:**
1. Check for UPX compression: look for "UPX!" strings
2. Check for statically linked binaries (still valid for QEMU)
3. Run `find . -type f -exec file {} \; | grep -i elf` to find ELFs

#### Issue: "encryption detected"

**Causes:**
- Vendor-specific encryption (D-Link SHRS, Tenda IMG, TP-Link encrypted firmware)

**Tools for decryption:**
- `firmware-mod-kit` has decryptors for common vendors
- `dlink-decrypt` for D-Link encrypted firmware
- `tplink-safeloader` for TP-Link
- Custom Python scripts for vendor-specific encryption

#### Preparing the Rootfs for Upload

Once you have an extracted root filesystem, prepare it for upload:

```bash
# Navigate to the extracted rootfs
cd extracted/_firmware.bin.extracted/squashfs-root/

# Optional: Remove large unnecessary files to reduce size
rm -rf opt/ var/cache/ tmp/*

# Optional: Fix broken symlinks
find . -type l ! -exec test -e {} \; -exec rm {} \;

# Create archive
tar -czf /tmp/rootfs.tar.gz .

# Upload to the agent
curl -X POST http://localhost:9100/api/upload_rootfs \
  -F "file=@/tmp/rootfs.tar.gz"
```

---

## Emulation Workflows

### User-Mode QEMU (Simple Services)

User-mode QEMU is the primary emulation mode. It works by translating the target binary's instructions to the host CPU and translating system calls.

**When to use:**
- Emulating a single binary (httpd, telnetd, custom daemon)
- The binary only needs filesystem access (no kernel modules, no /dev access)
- Quick one-off analysis

**How user-mode QEMU works:**

```
┌──────────────────────────────────────────┐
│            Host (x86_64)                  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │      qemu-mipsel-static            │  │
│  │                                    │  │
│  │  - Translates MIPS instructions    │  │
│  │  - Translates MIPS syscalls to     │  │
│  │    host syscalls                   │  │
│  │  - -L <rootfs>: maps / to rootfs  │  │
│  │                                    │  │
│  │  ┌──────────────────────────────┐  │  │
│  │  │  MIPSEL binary (httpd)       │  │  │
│  │  │  - Thinks it runs on MIPS    │  │  │
│  │  │  - Uses /lib, /usr/lib, etc. │  │  │
│  │  │  - Binds TCP port            │  │  │
│  │  └──────────────────────────────┘  │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Network: binary binds to 127.0.0.1:PORT │
└──────────────────────────────────────────┘
```

**Basic workflow:**

```bash
# 1. Upload rootfs
ROOTFS_ID=$(curl -s -X POST http://localhost:9100/api/upload_rootfs \
  -F "file=@rootfs.tar.gz" | jq -r '.rootfs_id')

# 2. Check architecture
curl -s http://localhost:9100/api/health | jq '.qemu'

# 3. Start the binary
SERVICE_ID=$(curl -s -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8080
  }" | jq -r '.service_id')

# 4. Wait and probe
sleep 3
curl -s -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 8080, "protocol": "http"}'
```

### System-Mode QEMU (Full OS Boot)

For more complex firmware that requires full OS boot (kernel modules, device nodes, init scripts).

**When to use:**
- Firmware that requires kernel boot and init system
- Services that depend on kernel modules (/dev/ devices)
- Multi-process firmware with complex startup dependencies
- Firmware requiring specific hardware interactions

**Limitations:** Currently not natively supported by the Emulation Agent API. System-mode QEMU requires:
- Kernel image matching the target architecture
- Device tree (for ARM)
- Root filesystem image (not just binary + libs)
- Network configuration (tap/bridge)

**Manual system-mode approach (for reference):**

```bash
# Extract kernel from firmware
dd if=firmware.bin of=kernel.bin bs=1 skip=<kernel_offset> count=<kernel_size>

# Create QEMU disk image
qemu-img create -f raw disk.img 256M
sudo mkfs.ext3 disk.img
sudo mount -o loop disk.img /mnt/qemu
sudo cp -a squashfs-root/* /mnt/qemu/
sudo umount /mnt/qemu

# Boot in QEMU system mode
qemu-system-mips \
  -M malta \
  -kernel kernel.bin \
  -hda disk.img \
  -nographic \
  -netdev user,id=net0,hostfwd=tcp::8080-:80 \
  -device e1000,netdev=net0 \
  -append "root=/dev/hda1 console=ttyS0 init=/sbin/init"
```

### Hybrid Approach

For research, a hybrid approach often works best:

1. **Initial exploration**: Extract firmware, identify binaries, check dependencies with user-mode QEMU
2. **User-mode for simple services**: Start individual binaries (httpd, telnetd) with user-mode QEMU
3. **System-mode for complex services**: If user-mode fails, fall back to system-mode QEMU for full OS boot

**Decision tree:**

```
Firmware extracted
  │
  ├── Binary self-contained? ──Yes──> User-mode QEMU
  │     (single binary, no kernel deps)
  │
  ├── Binary needs /dev/ nodes? ──Yes──> System-mode QEMU
  │     (uses /dev/mtd*, /dev/gpio, etc.)
  │
  ├── Binary needs kernel modules? ──Yes──> System-mode QEMU
  │     (insmod/modprobe calls)
  │
  ├── Binary uses only filesystem? ──Yes──> User-mode QEMU
  │     (reads config files, writes logs)
  │
  └── Multi-binary startup chain? ──Yes──> System-mode QEMU
        (init scripts, daemon chains)
```

---

## Service-Specific Guides

### GoAhead Webserver Emulation

GoAhead is the most common embedded web server found in IoT devices (D-Link, Netgear, TP-Link).

**Common characteristics:**
- Binary usually named `httpd`, `goahead`, or `webs`
- Reads configuration from NVRAM (not regular config files)
- May require specific directory structure (`/web`, `/etc_ro`)
- Often uses `.asp` or `.cgi` files for dynamic content

**Emulation procedure:**

```bash
# 1. Upload rootfs
ROOTFS_ID=$(curl -s -X POST http://localhost:9100/api/upload_rootfs \
  -F "file=@squashfs-root.tar.gz" | jq -r '.rootfs_id')

# 2. Configure NVRAM (CRITICAL for GoAhead)
curl -X POST http://localhost:9100/api/nvram_config \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"config\": {
      \"lan_ipaddr\": \"192.168.0.1\",
      \"lan_netmask\": \"255.255.255.0\",
      \"http_port\": \"80\",
      \"http_username\": \"admin\",
      \"http_password\": \"admin\",
      \"product_name\": \"DIR-882\",
      \"firmware_version\": \"1.20\",
      \"runtime_server\": \"GoAhead-Webs\"
    }
  }"

# 3. Start GoAhead
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8080,
    \"env_vars\": {
      \"LD_LIBRARY_PATH\": \"/lib:/usr/lib:/usr/local/lib\"
    }
  }"

# 4. Verify
sleep 3
curl -s http://127.0.0.1:8080/

# 5. Common URLs to test
curl -s http://127.0.0.1:8080/login.asp
curl -s http://127.0.0.1:8080/goform/  # GoAhead form handler
curl -s http://127.0.0.1:8080/cgi-bin/  # CGI directory
```

**Common GoAhead issues:**

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| "Cannot get NVRAM" error | NVRAM config missing | Run `/api/nvram_config` |
| CGI scripts return 500 | Missing CGI dependencies | Check with `ldd /web/cgi-bin/*.cgi` |
| ASP pages render as text | GoAhead compiled without ASP support | Check binary build flags |
| Port already in use | Multiple instances | Use different port or stop existing |

### Busybox httpd Emulation

Busybox includes a minimal HTTP server used in many embedded devices.

**Emulation procedure:**

```bash
# 1. Start busybox httpd
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/bin/busybox\",
    \"binary_name\": \"busybox\",
    \"args\": [\"httpd\", \"-f\", \"-p\", \"8080\", \"-h\", \"/www\"],
    \"port\": 8080
  }"

# 2. Test basic request
curl -s http://127.0.0.1:8080/

# 3. Test CGI (if compiled with CGI support)
curl -s http://127.0.0.1:8080/cgi-bin/test.cgi

# 4. Common busybox httpd flags:
# -f        Run in foreground
# -p PORT   Listen port
# -h DIR    Document root
# -u USER   Set uid
# -v        Verbose
```

**Common busybox httpd issues:**

- Busybox httpd may not be compiled with CGI support
- Document root may not exist; create `/www` directory if needed
- Busybox needs to be the target architecture binary, not the host busybox

### Telnetd Emulation

Many IoT devices run telnetd for remote management.

**Emulation procedure:**

```bash
# 1. Start telnetd
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/telnetd\",
    \"binary_name\": \"telnetd\",
    \"args\": [\"-F\", \"-p\", \"2323\"],
    \"port\": 2323
  }"

# 2. Probe telnet
curl -s -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 2323, "protocol": "telnet"}'

# 3. Manual telnet connection
telnet 127.0.0.1 2323
# Or: nc 127.0.0.1 2323
```

**Telnetd options:**

```
-F          Run in foreground
-p PORT     Listen port
-l LOGIN    Login program (default: /bin/login)
-i          Insecure mode (no auth)
```

### Proprietary IoT Services

Generic approach for unknown proprietary services:

```bash
# Step 1: Identify the binary
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"find / -type f -executable | head -20\",
    \"timeout\": 10
  }"

# Step 2: Analyze binary properties
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"file /usr/sbin/mystery_daemon && readelf -h /usr/sbin/mystery_daemon\",
    \"timeout\": 10
  }"

# Step 3: Check dependencies
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"ldd /usr/sbin/mystery_daemon 2>&1 || echo 'static binary'\",
    \"timeout\": 10
  }"

# Step 4: Check for strings (config file paths, NVRAM keys, etc.)
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"strings /usr/sbin/mystery_daemon | grep -E '(nvram|/etc/|/tmp/|port|config)' | head -30\",
    \"timeout\": 10
  }"

# Step 5: Try running with strace
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/mystery_daemon\",
    \"binary_name\": \"mystery_daemon\",
    \"args\": [],
    \"port\": 9001,
    \"strace\": true
  }"

# Step 6: Analyze crash/strace output
SERVICE_ID="svc-xxxxx"
curl -s http://localhost:9100/api/service/$SERVICE_ID | jq '.stderr_tail'
```

---

## NVRAM Configuration Guide

### When NVRAM is Needed

NVRAM (Non-Volatile RAM) is a key-value configuration system used by many embedded Linux systems. The Emulation Agent simulates NVRAM by writing a configuration file that the emulated binary reads.

**Your firmware needs NVRAM if the binary:**
- Crashes with "Cannot get NVRAM" or "nvram_get" errors
- References `libnvram.so` in its library dependencies
- Contains strings like `nvram_get`, `nvram_set`, `nvram_bufget`
- Is a GoAhead-based web server (very common)
- Was extracted from a D-Link, Tenda, TP-Link, or Netgear router

**Signs of NVRAM issues in QEMU output:**

```
Error: cannot open nvram
nvram_get: unable to open /etc_ro/nvram.conf
panic: nvram_init() failed
Segmentation fault (nvram_get returned NULL)
```

### Template System

The Emulation Agent provides pre-built NVRAM templates for common device types:

```bash
# List available templates
curl http://localhost:9100/api/nvram_templates
```

Common NVRAM keys by device type:

**Router (generic):**
```json
{
  "lan_ipaddr": "192.168.0.1",
  "lan_netmask": "255.255.255.0",
  "wan_ipaddr": "0.0.0.0",
  "wan_netmask": "0.0.0.0",
  "http_port": "80",
  "http_username": "admin",
  "http_password": "admin",
  "telnet_enable": "1",
  "ssh_enable": "0",
  "product_name": "Router",
  "firmware_version": "1.0.0",
  "default_language": "EN"
}
```

**IP Camera:**
```json
{
  "lan_ipaddr": "192.168.0.100",
  "lan_netmask": "255.255.255.0",
  "http_port": "80",
  "rtsp_port": "554",
  "onvif_port": "8899",
  "http_username": "admin",
  "http_password": "12345",
  "product_model": "IPC-001",
  "firmware_version": "2.0.1",
  "sensor_type": "OV9712"
}
```

**NAS Device:**
```json
{
  "lan_ipaddr": "192.168.0.200",
  "lan_netmask": "255.255.255.0",
  "http_port": "80",
  "https_port": "443",
  "samba_enable": "1",
  "ftp_enable": "1",
  "admin_username": "admin",
  "admin_password": "netgear1",
  "product_name": "ReadyNAS",
  "disk_count": "2"
}
```

### Manual Configuration

To discover which NVRAM keys a binary needs:

```bash
# 1. Extract all NVRAM key references from the binary
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"strings /usr/sbin/httpd | grep -E 'nvram_(get|set|bufget|commit|unset)' | sort -u\",
    \"timeout\": 10
  }"

# 2. From the binary's src code or decompilation (Ghidra/IDA), find the keys it reads
# Common string patterns:
#   nvram_get("lan_ipaddr")
#   nvram_bufget(RT2860_NVRAM, "lan_ipaddr")
#   GetNvramVar("http_port")

# 3. Check for default config files in the rootfs
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"find /etc /etc_ro /tmp -name '*nvram*' -o -name '*default*' -o -name '*conf*' 2>/dev/null\",
    \"timeout\": 10
  }"

# 4. Look at init scripts for NVRAM initialization
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"cat /etc/init.d/rcS 2>/dev/null || cat /etc/rc 2>/dev/null || echo 'no init script found'\",
    \"timeout\": 10
  }"

# 5. Create a comprehensive NVRAM config based on findings
curl -X POST http://localhost:9100/api/nvram_config \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"config\": {
      ... all discovered keys here ...
    }
  }"
```

---

## Debugging Emulated Services

### QEMU Strace

Use QEMU's built-in strace to trace system calls:

```bash
# Start service with strace enabled
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8080,
    \"strace\": true
  }"

# Check strace output
SERVICE_ID="svc-xxxxx"
curl -s http://localhost:9100/api/service/$SERVICE_ID | jq '.stdout_tail'

# Expected strace output example:
# openat(AT_FDCWD, "/etc_ro/nvram.conf", O_RDONLY) = 3
# read(3, "lan_ipaddr=192.168.0.1\n", 4096) = 22
# socket(AF_INET, SOCK_STREAM, 0) = 4
# bind(4, {sa_family=AF_INET, sin_port=htons(80)}, 16) = 0
# listen(4, 10) = 0
# accept(4, ...) = 5
```

**Manual strace (via /api/exec):**

```bash
# If the emulated rootfs has strace (most don't), use QEMU's -strace flag
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"strace /usr/sbin/httpd -p 8080 2>&1 &\",
    \"timeout\": 10
  }"
```

### GDB Remote Debugging

For deep binary analysis, attach GDB to the QEMU process:

```bash
# Start QEMU with GDB stub (on the VM directly, not via API)
qemu-mipsel-static -g 1234 -L ./rootfs/abc123/ ./rootfs/abc123/usr/sbin/httpd

# In another terminal, connect GDB
gdb-multiarch \
  -ex "target remote localhost:1234" \
  -ex "set architecture mips" \
  -ex "set sysroot ./rootfs/abc123/" \
  -ex "file ./rootfs/abc123/usr/sbin/httpd"

# Common GDB commands for embedded debugging:
(gdb) info registers          # View MIPS registers
(gdb) x/10i $pc               # Disassemble at program counter
(gdb) bt                      # Backtrace
(gdb) info sharedlibrary      # Loaded shared libraries
(gdb) break *0x00401234       # Set breakpoint at address
(gdb) continue                # Resume execution
```

**GDB with multiarch:**

```bash
# Install multi-architecture GDB
sudo apt-get install gdb-multiarch

# For MIPSEL debugging
gdb-multiarch -ex "set arch mips:isa32r2" -ex "set endian little"

# For ARM debugging
gdb-multiarch -ex "set arch armv7" -ex "target remote localhost:1234"
```

### Memory Analysis

```bash
# Using QEMU's built-in monitor
# Add "-monitor stdio" to QEMU command for interactive debugging

# Dump process memory via /proc (on the host)
PID=45821
cat /proc/$PID/maps        # Memory mappings
cat /proc/$PID/status      # Process status

# Read QEMU process memory
dd if=/proc/$PID/mem bs=1 skip=$((0x400000)) count=1024 2>/dev/null | xxd

# Use pmap for memory usage
pmap -x $PID
```

### Common Debugging Patterns

**Binary immediately crashes:**

```bash
# Check if it's a permission issue
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"ls -la /usr/sbin/httpd\",
    \"timeout\": 5
  }"

# Check if libraries are missing
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"ldd /usr/sbin/httpd 2>&1\",
    \"timeout\": 10
  }"
```

**Binary runs but service not reachable:**

```bash
# Check if it's listening
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"sleep 5 && netstat -tlnp 2>/dev/null || ss -tlnp 2>/dev/null || echo 'netstat not available'\",
    \"timeout\": 15
  }"

# Try binding to 0.0.0.0 instead of a specific IP
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8080,
    \"env_vars\": {\"BIND_ADDR\": \"0.0.0.0\"}
  }"
```

**Service responds with unexpected content:**

```bash
# Capture full HTTP response
curl -v http://127.0.0.1:8080/ 2>&1

# Check web root directory
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"ls -la /web/ /www/ /htdocs/ /var/www/ 2>/dev/null\",
    \"timeout\": 5
  }"
```

---

## Fuzzing Integration

### Network Fuzzing Setup

Once a service is emulated and accessible, you can fuzz it over the network:

```bash
# 1. Start the service
SERVICE_ID=$(curl -s -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [\"-p\", \"8080\"],
    \"port\": 8080
  }" | jq -r '.service_id')

# 2. Wait for service to be ready
sleep 5
curl -s -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 8080, "protocol": "http"}'

# 3. Run fuzzer against the emulated service
# Example: boofuzz for HTTP fuzzing
pip install boofuzz
python3 << 'EOF'
from boofuzz import *

session = Session(
    target=Target(connection=SocketConnection("127.0.0.1", 8080, proto="tcp"))
)

s_initialize("http_request")
s_string("GET", fuzzable=False)
s_delim(" ", fuzzable=False)
s_string("/")
s_string("?", fuzzable=False)
s_string("param1=")
s_string("value1")
s_static("\r\n\r\n")

session.connect(s_get("http_request"))
session.fuzz()
EOF

# 4. Check service status after fuzzing
curl -s http://localhost:9100/api/service/$SERVICE_ID
# Look for: crashes, restarts, unexpected exit codes
```

### AFL++ QEMU Mode

AFL++ can fuzz emulated binaries using QEMU user-mode instrumentation:

```bash
# 1. Install AFL++
sudo apt-get install afl++

# 2. Prepare test inputs
mkdir -p fuzz_input fuzz_output
echo "GET / HTTP/1.0\r\n\r\n" > fuzz_input/test1.txt

# 3. Run AFL++ in QEMU mode
# Note: This runs directly on the host, not via the API
AFL_QEMU_PERSISTENT=1 \
AFL_QEMU_PERSISTENT_ADDR=0x00401234 \
afl-fuzz -Q \
  -i fuzz_input \
  -o fuzz_output \
  -- ./rootfs/abc123/usr/sbin/httpd @@

# 4. Monitor results
afl-whatsup fuzz_output
```

**Custom AFL++ fuzzing harness:**

```python
# harness.py - Run with AFL_QEMU_CUSTOM_BIN=1
import socket
import sys

def harness():
    # Read input from stdin (AFL provides via @@)
    data = sys.stdin.buffer.read()

    # Send to emulated service
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", 8080))
    sock.send(data)

    # Receive response
    response = sock.recv(4096)
    sock.close()

    return 0

if __name__ == "__main__":
    sys.exit(harness())
```

### Custom Fuzzing Harness

Building a custom Python fuzzing harness against the emulated service:

```python
#!/usr/bin/env python3
"""Custom fuzzing harness for emulated IoT services."""

import socket
import random
import time
import sys
import struct

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 8080

# Fuzzing grammar for HTTP requests
HTTP_METHODS = [b"GET", b"POST", b"HEAD", b"PUT", b"DELETE", b"OPTIONS",
                b"TRACE", b"CONNECT", b"PATCH",
                b"\x00GET", b"GET\x00", b"\xff\xff\xff\xff"]

PATHS = [b"/", b"/login.cgi", b"/admin/", b"/cgi-bin/", b"/goform/",
         b"/" + b"A" * 100, b"/" + b"A" * 1000, b"/..%2f..%2f..%2fetc/passwd",
         b"/%00index.html", b"/%20"]

HEADERS = [
    b"Host: 127.0.0.1\r\n",
    b"Content-Length: 100\r\n",
    b"User-Agent: Mozilla/5.0\r\n",
    b"Authorization: Basic YWRtaW46YWRtaW4=\r\n",
    b"X-Forwarded-For: 127.0.0.1\r\n",
    b"Cookie: session=" + b"A" * 50 + b"\r\n",
    b"Accept: */*\r\n",
    b"Connection: keep-alive\r\n",
]

BODIES = [
    b"",
    b"username=admin&password=admin",
    b"a" * 1000,
    b"\x00" * 100,
    b"%s" * 100,
]

CORRUPTIONS = [
    lambda d: d.replace(b" ", b"\t"),
    lambda d: d.replace(b"\r\n", b"\n"),
    lambda d: d[:len(d)//2],
    lambda d: d * 100,
    lambda d: struct.pack(">I", len(d)) + d,
]


def build_request():
    """Build a random HTTP request."""
    method = random.choice(HTTP_METHODS)
    path = random.choice(PATHS)
    version = random.choice([b"HTTP/1.0", b"HTTP/1.1", b"HTTP/9.9"])

    request = method + b" " + path + b" " + version + b"\r\n"
    request += b"Host: 127.0.0.1\r\n"
    request += random.choice(HEADERS)
    request += random.choice(HEADERS)

    if random.random() < 0.3:
        request += b"Content-Length: " + str(len(random.choice(BODIES))).encode() + b"\r\n"

    request += b"\r\n"
    request += random.choice(BODIES)

    # Apply random corruption
    if random.random() < 0.1:
        corruption = random.choice(CORRUPTIONS)
        request = corruption(request)

    return request


def send_and_monitor(request):
    """Send request and check for crashes."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((TARGET_HOST, TARGET_PORT))
        sock.send(request)

        # Try to receive
        try:
            response = sock.recv(4096)
        except socket.timeout:
            response = b"TIMEOUT"

        sock.close()
        return True, response
    except ConnectionRefusedError:
        return False, b"CONNECTION_REFUSED"
    except Exception as e:
        return False, str(e).encode()


def main():
    """Main fuzzing loop."""
    iteration = 0
    crashes = []
    restart_count = 0

    while True:
        iteration += 1
        request = build_request()
        success, response = send_and_monitor(request)

        if not success:
            crash_info = {
                "iteration": iteration,
                "request_hex": request.hex(),
                "request_len": len(request),
                "response": response.decode(errors="replace"),
                "timestamp": time.time()
            }
            crashes.append(crash_info)
            print(f"[CRASH #{len(crashes)}] Iteration {iteration}, "
                  f"Len: {len(request)}, Response: {response[:50]}")

            # Save crash
            with open(f"crash_{len(crashes):04d}.bin", "wb") as f:
                f.write(request)

            # Check if service needs restart
            if restart_count < 5:
                restart_count += 1
                time.sleep(2)
                # Here: call API to restart service
                continue
            else:
                print("Too many crashes, exiting")
                break

        if iteration % 1000 == 0:
            print(f"[INFO] {iteration} iterations, {len(crashes)} crashes")


if __name__ == "__main__":
    main()
```

### Fuzzing Workflow Integration

```bash
#!/bin/bash
# Complete fuzzing workflow with the Emulation Agent

AGENT="http://localhost:9100"
ROOTFS_ID="abc123"
BINARY="/usr/sbin/httpd"
PORT=8080

# 1. Start fresh service
echo "Starting service for fuzzing..."
SERVICE_ID=$(curl -s -X POST $AGENT/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"$BINARY\",
    \"binary_name\": \"$(basename $BINARY)\",
    \"args\": [\"-p\", \"$PORT\"],
    \"port\": $PORT
  }" | jq -r '.service_id')
echo "Service ID: $SERVICE_ID"

# 2. Run fuzzer
echo "Starting fuzzer..."
python3 fuzz_harness.py &
FUZZ_PID=$!

# 3. Monitor loop
while kill -0 $FUZZ_PID 2>/dev/null; do
    sleep 10
    STATUS=$(curl -s $AGENT/api/service/$SERVICE_ID | jq -r '.status')

    if [ "$STATUS" != "running" ]; then
        echo "Service crashed! Restarting..."
        # Save crash artifacts
        curl -s $AGENT/api/service/$SERVICE_ID > "crash_$(date +%s).json"

        # Restart
        SERVICE_ID=$(curl -s -X POST $AGENT/api/start_service \
          -H "Content-Type: application/json" \
          -d "{
            \"rootfs_id\": \"$ROOTFS_ID\",
            \"binary_path\": \"$BINARY\",
            \"binary_name\": \"$(basename $BINARY)\",
            \"args\": [\"-p\", \"$PORT\"],
            \"port\": $PORT
          }" | jq -r '.service_id')

        sleep 3
    fi
done

# 4. Cleanup
curl -X POST $AGENT/api/stop_service/$SERVICE_ID
echo "Fuzzing complete."
```

---

## Best Practices and Tips

### General Best Practices

1. **Always configure NVRAM first**: Many embedded services crash without NVRAM. Make this step 1 after upload.

2. **Check library dependencies**: Use `ldd` via `/api/exec` before starting a service to catch missing libraries.

3. **Start with strace**: When debugging, enable strace (`"strace": true`) to see exactly which syscalls fail.

4. **Use specific ports**: Avoid port conflicts by specifying unique ports for each service. Keep a port map.

5. **Monitor crash rates**: High crash rates usually indicate missing dependencies or configuration.

6. **Save working configurations**: Once you find a working NVRAM config for a particular device family, save it as a template.

7. **Use version control for rootfs**: Keep extracted rootfs in version control or backup storage.

8. **Document findings**: Record which services worked, which didn't, and the required configurations.

### Performance Tips

1. **Use SSD storage**: Rootfs extraction and file operations are I/O bound.
2. **Limit concurrent services**: More than 10-15 concurrent QEMU processes can cause memory pressure.
3. **Clean up regularly**: Remove old rootfs and stopped service artifacts.
4. **Pre-warm QEMU**: The first QEMU invocation loads libraries; subsequent ones are faster.

### Security Tips

1. **Run in a VM**: Always run the Emulation Agent in a dedicated VM, not on your host OS.
2. **Use SSH tunnels**: Never expose the API port to the public internet.
3. **Network isolation**: Use a dedicated VLAN or network namespace for the agent.
4. **Regular updates**: Keep QEMU and system packages updated.
5. **Monitor resource usage**: Set up alerts for unusual CPU/memory/network activity.

### Troubleshooting Checklist

Before asking for help, go through this checklist:

- [ ] QEMU static binaries installed and in PATH?
- [ ] Rootfs properly extracted (check for ELF binaries)?
- [ ] Architecture correctly detected?
- [ ] NVRAM configured (if applicable)?
- [ ] Library dependencies satisfied (`ldd <binary>`)?
- [ ] Port not already in use?
- [ ] Server has enough free disk space and memory?
- [ ] Check server logs for detailed error messages?

### Quick Reference Card

```bash
# Essential commands at a glance

# Health
curl http://localhost:9100/api/health

# Upload
curl -X POST http://localhost:9100/api/upload_rootfs -F "file=@rootfs.tar.gz"

# Start
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"ID","binary_path":"/usr/sbin/httpd","port":8080}'

# Status
curl http://localhost:9100/api/service/SERVICE_ID

# Probe
curl -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"host":"127.0.0.1","port":8080,"protocol":"http"}'

# Exec
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"ID","command":"ls /usr/sbin/","timeout":10}'

# NVRAM
curl -X POST http://localhost:9100/api/nvram_config \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"ID","config":{"lan_ipaddr":"192.168.0.1"}}'

# Stop
curl -X POST http://localhost:9100/api/stop_service/SERVICE_ID

# List all
curl http://localhost:9100/api/services
```
