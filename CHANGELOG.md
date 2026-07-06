# Changelog

All notable changes to the Emulation Agent project.

## [1.0.0] - 2026-07-02

### Added
- Initial release of the Emulation Agent firmware emulation vulnerability research system
- FastAPI server with 10 REST API endpoints for firmware emulation and analysis
- QEMU user-mode emulation for MIPS (big-endian), MIPSEL (little-endian), ARM (32-bit), AARCH64 (64-bit), and x86_64 architectures
- Firmware extraction pipeline with automatic architecture detection via ELF header analysis
- Service lifecycle management: upload rootfs, start/stop services, process health monitoring
- Service probing system supporting TCP connect, HTTP GET, and Telnet banner detection
- Remote command execution in emulated environments via QEMU user-mode + busybox
- NVRAM configuration emulation for resolving common firmware boot failures (GoAhead, etc.)
- HTTP client library for programmatic interaction with the emulation agent from remote machines
- CLI tool (`emulation-agent`) for managing the server from the command line
- Docker support with pre-built Dockerfile for containerized deployment
- Fuzzing harness integration with AFL++ QEMU mode
- Claude Code agent definitions including `emulate-and-analyze` workflow
- Agent workflows: `firmware-emulator` agent and `firmware-vuln-researcher` agent
- Comprehensive documentation: README, Architecture, User Guide, API Reference, Vulnerability Research Workflow
- Support for three operational modes: Direct Hardware, Emulation Agent, and Static Only
- SSH tunnel support for secure remote access to the emulation agent
- Auto-discovery of emulation services within the vulnagent ecosystem
