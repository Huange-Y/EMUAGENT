---
name: emulate-and-analyze
description: Full firmware emulation and vulnerability analysis pipeline. Extract, emulate, probe, and analyze firmware for vulnerabilities.
phases:
  - title: Extract
    detail: Extract firmware and detect architecture
  - title: Emulate
    detail: Start QEMU emulation for target services
  - title: Probe
    detail: Verify services are responding
  - title: Analyze
    detail: Static and dynamic vulnerability analysis
  - title: Report
    detail: Compile vulnerability findings
---

# Emulate & Analyze Workflow

## Inputs
- `firmware_path`: Path to firmware image or rootfs
- `target_services`: List of binaries to emulate (default: auto-discover)
- `analysis_depth`: quick | standard | deep (default: standard)

## Step 1: Extract
```bash
# Use binwalk to extract firmware
binwalk -Me <firmware_path> -d /tmp/fw_extract
# Find the rootfs (squashfs, cpio, etc.)
find /tmp/fw_extract -name "bin" -type d
# Detect architecture
file /tmp/fw_extract/*/bin/busybox
```

## Step 2: Emulate
For each target service:
```bash
qemu-<arch>-static -L <rootfs> -strace <binary> 2>&1 | tee emu.log &
```

## Step 3: Probe
```bash
# Wait for service to start
sleep 2
# HTTP probe
curl -v http://localhost:<port>/ 2>&1
# TCP banner
echo "" | nc localhost <port>
```

## Step 4: Analyze
- Static: strings, objdump, Ghidra headless
- Dynamic: strace from QEMU, network capture
- Fuzzing: AFL++ with QEMU mode if available

## Step 5: Report
Compile findings into structured vulnerability report.

## Parallelization
Steps 2-3 can run in parallel for different services.
Step 4 analysis can fan out across multiple binaries.
