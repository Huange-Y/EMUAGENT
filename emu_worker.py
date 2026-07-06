#!/usr/bin/env python3
"""Emu Agent Worker — process cmd.emu.*.json, run QEMU services"""
import json, os, sys, time, subprocess, shutil
from pathlib import Path
sys.path.insert(0, '/home/art/emulation_agent')
from task_manager import QDIR, log

ROOTFS_BASE = Path('/tmp/emulation_agent/rootfs')

def start_telnetd(rootfs):
    """Start busybox telnetd as emulated service"""
    bb = rootfs / 'bin/busybox'
    if not bb.exists():
        log(f'No busybox in {rootfs}')
        return None
    
    log_path = '/tmp/telnetd_worker.log'
    cmd = ['setsid', 'qemu-mipsel-static', '-L', str(rootfs),
           '-E', 'HOME=/', '-E', 'PATH=/bin:/sbin:/usr/bin:/usr/sbin',
           str(bb), 'telnetd', '-l', '/bin/sh', '-p', '5555']
    with open(log_path, 'w') as f:
        p = subprocess.Popen(cmd, stdout=f, stderr=f, cwd=str(rootfs))
    log(f'Telnetd started PID={p.pid}')
    time.sleep(1.5)
    return p

def probe_port(port, timeout=2):
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(('127.0.0.1', port))
        s.close()
        return True
    except: return False

def process(cmd_file):
    try:
        data = json.loads(cmd_file.read_text())
        cmd_file.unlink()
    except: return
    
    action = data.get('type',''); tid = data.get('task_id','')
    vendor = data.get('vendor',''); model = data.get('model','')
    log(f'Processing: {action} {vendor} {model} ({tid[:8]})')
    
    if action != 'download':
        log(f'Unknown action: {action}')
        return
    
    # Find rootfs
    rootfs = None
    for d in ROOTFS_BASE.iterdir():
        if d.is_dir() and (d / 'bin/busybox').exists():
            rootfs = d
            break
    if not rootfs:
        # Auto-extract if firmware available
        rootfs = ROOTFS_BASE / 'dir816'  # fallback to known rootfs
    
    svcs = []
    eps = []
    
    # Try GoAhead first
    goahead = rootfs / 'bin/goahead'
    if goahead.exists():
        p = subprocess.Popen(
            ['qemu-mipsel-static', '-L', str(rootfs), '-E', 'HOME=/',
             '-E', 'PATH=/bin:/sbin:/usr/bin:/usr/sbin', str(goahead)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(rootfs), preexec_fn=os.setsid
        )
        time.sleep(1)
        if probe_port(80, 1):
            svcs.append({'name': 'goahead', 'port': 80, 'status': 'running'})
            eps.append('http://127.0.0.1:80')
            log('GoAhead running on :80')
        else:
            svcs.append({'name': 'goahead', 'port': 80, 'status': 'crashed'})
    
    # Always start telnetd as reliable fallback
    p = start_telnetd(rootfs)
    if p and probe_port(5555):
        svcs.append({'name': 'telnetd', 'port': 5555, 'status': 'running'})
        eps.append('telnet://127.0.0.1:5555')
        log('Telnetd running on :5555')
    else:
        svcs.append({'name': 'telnetd', 'port': 5555, 'status': 'crashed'})
    
    result = {
        'type': 'env_ready',
        'task_id': tid,
        'vendor': vendor,
        'model': model,
        'architecture': 'mips',
        'endianness': 'little',
        'services': svcs,
        'scan_endpoints': eps,
        'rootfs_id': rootfs.name,
        'status': 'ready' if any(s['status']=='running' for s in svcs) else 'failed',
        'notes': f'Services: {len([s for s in svcs if s["status"]=="running"])} running'
    }
    (QDIR / f'vulnagent.env_ready.{tid}.json').write_text(json.dumps(result, indent=2))
    log(f'env_ready: {len(eps)} endpoints, {len(svcs)} services')

def main():
    log('Emu Agent Worker started')
    log(f'Rootfs base: {ROOTFS_BASE}')
    log(f'Watching: {QDIR}/cmd.emu.*.json')
    seen = set()
    while True:
        for f in sorted(QDIR.glob('cmd.emu.*.json')):
            if f.name in seen: continue
            seen.add(f.name)
            try: process(f)
            except Exception as e: log(f'Error: {e}')
        if len(seen) > 100: seen.clear()
        time.sleep(2)

if __name__ == '__main__':
    main()
