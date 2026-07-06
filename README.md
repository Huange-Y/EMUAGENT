# EMUAGENT — QEMU Firmware Emulation Backend

Firmware 二进制文件的 QEMU 仿真 REST API 服务。用于在沙盒环境中运行路由器/IoT 固件，对外暴露服务接口供漏洞扫描工具探测。

## Quick Start

```bash
git clone https://github.com/Huange-Y/EMUAGENT.git && cd EMUAGENT
pip install -r requirements.txt
python3 -m uvicorn server:app --host 0.0.0.0 --port 9100
```

或一键部署：
```bash
EMU_HOST=your-vm-ip bash deploy.sh
```

---

## 与 vulnagent 协作

emu-agent 是为 [vulnagent](https://github.com/Huange-Y/Vulnagent) 配套设计的动态验证后端。两者通过 HTTP API 协作：

```
┌─────────────────────┐                    ┌─────────────────────┐
│     vulnagent       │                    │     emu-agent       │
│  (Windows/Linux)    │                    │  (Linux VM :9100)   │
│                     │                    │                     │
│  orchestrator.py    │                    │  server.py          │
│    ↓                │                    │    ↓                │
│  backend.py         │  ─── HTTP ───→     │  firmware_manager   │
│    ↓                │  ←── JSON ───      │    ↓                │
│  client.py          │                    │  qemu_runner        │
│                     │                    │    ↓                │
│  DiscoveryAgent     │                    │  probe.py           │
│    elf_surface_scan │                    │    ↓                │
│  ExploitAgent       │                    │  nvram.py           │
│    PoC 回放验证      │                    │                     │
└─────────────────────┘                    └─────────────────────┘
```

### 完整协作流程

**第一步 — vulnagent 提取固件并上传到仿真环境：**
```
vulnagent 提取 rootfs → POST /api/upload_rootfs → 返回 rootfs_id
vulnagent 探测架构    → POST /api/detect_arch   → 返回 arch + endian
```

**第二步 — vulnagent 在 QEMU 中启动目标服务：**
```
vulnagent 的 elf_surface_scan 发现 httpd 有 system()+popen() 调用
    ↓
POST /api/start_service {
    "rootfs_id": "fw_abc123",
    "binary_name": "httpd",
    "binary_path": "/usr/sbin/httpd",
    "args": "-p 8080",
    "port": 8080
}
    → emu-agent 用 qemu-mipsel-static 启动 httpd
    → 返回 {"service_id": "svc_xyz", "status": "running", "port": 8080}
```

**第三步 — vulnagent 探测仿真服务确认可达：**
```
POST /api/probe {
    "rootfs_id": "fw_abc123",
    "port": 8080,
    "protocol": "http"
}
    → emu-agent TCP 连接检测 + HTTP banner 抓取
    → 返回 {"reachable": true, "banner": "GoAhead/2.5", "http_status": 200}
    → vulnagent 将此结果写入 VerificationResult，标记为 VERIFIED
```

**第四步 — vulnagent ExploitAgent 在仿真环境验证漏洞：**
```
POST /api/exec {
    "rootfs_id": "fw_abc123",
    "command": "curl 'http://127.0.0.1:8080/cgi-bin/vuln.cgi?cmd=id'"
}
    → 在 QEMU 环境中执行利用请求

POST /api/exec {
    "rootfs_id": "fw_abc123",
    "command": "cat /tmp/pwned"
}
    → 检查利用是否成功（命令执行后写入的文件）
    → 如果成功，vulnagent 将 finding 升级为 confirmed
```

**第五步 — vulnagent 清理：**
```
DELETE /api/rootfs/fw_abc123
    → 停止所有关联服务，删除 rootfs 目录
```

### vulnagent 配置

vulnagent 的 `config/settings.local.yaml` 中启用 emulation 后，上述五步完全自动：

```yaml
emulation:
  enabled: true
  agent_host: "your-vm-ip"   # emu-agent 所在机器 IP
  agent_port: 9100

  # 跨网络通过 SSH 隧道
  # ssh_host: "your-vm-host"
  # ssh_port: 22
  # ssh_user: "your-user"
  # ssh_key: "~/.ssh/id_ed25519"
```

配置后，vulnagent 管线在 Discovery 阶段发现漏洞后，自动调用：
1. `firmware_emulation_prepare` → 上传 rootfs 到 emu-agent
2. `firmware_emulation_launch_user` → 在 QEMU 中启动目标服务
3. `firmware_emulation_probe` → 确认服务可达
4. PoC 验证阶段自动在仿真环境中执行利用

vulnagent 的 `elf_surface_scan` 扫描结果中包含 `component_path` 字段，emu-agent 直接用它来定位二进制文件并在正确的 rootfs 环境下启动 QEMU。

### 无 vulnagent 时的独立使用

emu-agent 也可以独立使用 — 直接 curl 调用 API：

```bash
# 上传 rootfs
curl -X POST http://localhost:9100/api/upload_rootfs \
  -F "file=@rootfs.tar.gz"

# 启动 httpd
curl -X POST http://localhost:9100/api/start_service \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"fw_abc123","binary_name":"httpd","binary_path":"/usr/sbin/httpd","port":8080}'

# 探测
curl -X POST http://localhost:9100/api/probe \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"fw_abc123","port":8080,"protocol":"http"}'

# 在仿真环境中执行命令
curl -X POST http://localhost:9100/api/exec \
  -H "Content-Type: application/json" \
  -d '{"rootfs_id":"fw_abc123","command":"cat /etc/passwd"}'
```

---

## API 参考

### 健康检查
```
GET /api/health
→ {"status":"ok","binaries":{"mipsel":true,"arm":true,...},"services_running":3,"rootfs_count":1}
```

### Rootfs 管理
```
POST   /api/upload_rootfs          上传 rootfs tar.gz (multipart/form-data)
GET    /api/rootfs                  列出所有 rootfs
DELETE /api/rootfs/{rootfs_id}      删除 rootfs 及关联服务
POST   /api/detect_arch             Auto-detect CPU arch {"rootfs_id":"..."}
```

### 服务管理
```
POST /api/start_service            在 QEMU 中启动二进制
     Body: {"rootfs_id","binary_name","binary_path","args?","port?"}

GET  /api/services                 列出所有运行中的仿真服务

GET  /api/services/{id}/logs       获取服务 stdout/stderr

POST /api/stop_service/{id}        停止服务
```

### 探测与执行
```
POST /api/probe                    TCP/HTTP 可达性探测
     Body: {"rootfs_id","port","protocol?"}
     → {"reachable":true,"protocol":"http","banner":"GoAhead/2.5","latency_ms":12.3}

POST /api/exec                    在仿真环境执行命令
     Body: {"rootfs_id","command"}
     → {"stdout":"...","stderr":"...","return_code":0}
```

### NVRAM
```
POST /api/nvram_config            设置 NVRAM 变量
     Body: {"rootfs_id","config":{"lan_ipaddr":"192.168.1.1","http_lanport":"80"}}
```

---

## 支持架构

| 架构 | QEMU 用户态 | QEMU 系统态 |
|------|------------|------------|
| MIPS big | qemu-mips-static | qemu-system-mips |
| MIPS little | qemu-mipsel-static | qemu-system-mipsel |
| MIPS64 | qemu-mips64-static | qemu-system-mips64 |
| ARM little | qemu-arm-static | qemu-system-arm |
| ARM big | qemu-armeb-static | qemu-system-arm |
| AArch64 | qemu-aarch64-static | qemu-system-aarch64 |
| i386 | qemu-i386-static | qemu-system-i386 |
| x86_64 | qemu-x86_64-static | qemu-system-x86_64 |
| PowerPC | qemu-ppc-static | qemu-system-ppc |
| SPARC | qemu-sparc-static | qemu-system-sparc |

## 配置

`config.py` 支持 YAML 文件或环境变量覆盖。首次启动自动检测可用的 QEMU 二进制。

## License

MIT
