# EMUAGENT — QEMU Firmware Emulation Backend

Firmware 二进制文件的 QEMU 仿真 REST API 服务。接收 rootfs 上传，在沙盒环境中启动服务，探测端口，执行命令。

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

## API 参考

### 健康检查
```
GET /api/health
→ {"status":"ok","binaries":{"mipsel":true,"arm":true,...},"services_running":3,"rootfs_count":1}
```

### Rootfs 管理
```
POST /api/upload_rootfs
  上传 firmware rootfs tar.gz，返回 rootfs_id
  Content-Type: multipart/form-data
  Body: file=<tarball>

GET /api/rootfs
  列出所有已上传的 rootfs 镜像

DELETE /api/rootfs/{rootfs_id}
  删除指定 rootfs 及所有关联服务
```

### 架构检测
```
POST /api/detect_arch
  自动检测 rootfs 的 CPU 架构和字节序
  Body: {"rootfs_id":"fw_xxx"}
  → {"architecture":"mipsel","endianness":"little","confidence":0.98}
```

### 服务管理
```
POST /api/start_service
  在 QEMU 用户态模拟下启动二进制文件
  Body: {
    "rootfs_id":"fw_xxx",
    "binary_name":"httpd",
    "binary_path":"/usr/sbin/httpd",
    "args":"-p 8080",
    "port":8080
  }
  → {"service_id":"svc_xxx","status":"running","port":8080}

GET /api/services
  列出所有运行中的仿真服务
  → [{"service_id":"svc_xxx","binary_name":"httpd","port":8080,"status":"running",...}]

GET /api/services/{service_id}/logs
  获取仿真服务的 stdout/stderr 日志

POST /api/stop_service/{service_id}
  停止指定仿真服务
```

### 探测
```
POST /api/probe
  探测仿真服务的可达性和协议类型
  Body: {"rootfs_id":"fw_xxx","port":8080,"protocol":"http"}
  → {"reachable":true,"protocol":"http","banner":"GoAhead/2.5",...}
```

### 命令执行
```
POST /api/exec
  在仿真环境中执行任意命令
  Body: {"rootfs_id":"fw_xxx","command":"cat /etc/shadow"}
  → {"stdout":"root:$1$...","stderr":"","return_code":0}
```

### NVRAM 配置
```
POST /api/nvram_config
  设置仿真环境的 NVRAM 变量（影响固件行为）
  Body: {"rootfs_id":"fw_xxx","config":{"lan_ipaddr":"192.168.1.1","http_lanport":"80"}}
```

## 支持架构

| 架构 | QEMU 用户态 | QEMU 系统态 |
|------|------------|------------|
| MIPS (big) | qemu-mips-static | qemu-system-mips |
| MIPS (little) | qemu-mipsel-static | qemu-system-mipsel |
| MIPS64 | qemu-mips64-static | qemu-system-mips64 |
| ARM (little) | qemu-arm-static | qemu-system-arm |
| ARM (big) | qemu-armeb-static | qemu-system-arm |
| AArch64 | qemu-aarch64-static | qemu-system-aarch64 |
| i386 | qemu-i386-static | qemu-system-i386 |
| x86_64 | qemu-x86_64-static | qemu-system-x86_64 |
| PowerPC | qemu-ppc-static | qemu-system-ppc |
| SPARC | qemu-sparc-static | qemu-system-sparc |

## 配置

`config.py` 支持 YAML 文件 (`emulation_agent_config.yaml`) 或环境变量覆盖。

## 与 vulnagent 集成

vulnagent 通过 `emulation_agent/backend.py` → `client.py` → HTTP 调用 emu-agent：

```
vulnagent (Windows)                     emu-agent (Linux VM :9100)
  orchestrator.py                       server.py
    → backend.py ──HTTP──→              → firmware_manager (rootfs 提取)
      → client.py                       → qemu_runner (QEMU 进程管理)
        EmulationAgentClient            → probe (TCP/HTTP 探测)
                                        → nvram (NVRAM 模拟)
```

vulnagent 的 `settings.example.yaml` 中配置 emulation 段后自动发现。

## License

MIT
