"""
Configuration management for the Emulation Agent.

Loads settings from YAML config file, environment variables, and defaults.
Provides a singleton Config object used throughout the agent.
"""

import os
import yaml
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_CONFIG_PATHS = [
    Path("/etc/emulation_agent/config.yaml"),
    Path.home() / ".config" / "emulation_agent" / "config.yaml",
    Path("emulation_agent_config.yaml"),
]

# Supported QEMU architectures with their binary name patterns
SUPPORTED_ARCHES = {
    "mips": {
        "user": "qemu-mips-static",
        "system": "qemu-system-mips",
        "cpu": "MIPS",
    },
    "mipsel": {
        "user": "qemu-mipsel-static",
        "system": "qemu-system-mipsel",
        "cpu": "MIPSEL",
    },
    "mips64": {
        "user": "qemu-mips64-static",
        "system": "qemu-system-mips64",
        "cpu": "MIPS64",
    },
    "mips64el": {
        "user": "qemu-mips64el-static",
        "system": "qemu-system-mips64el",
        "cpu": "MIPS64EL",
    },
    "arm": {
        "user": "qemu-arm-static",
        "system": "qemu-system-arm",
        "cpu": "ARM",
    },
    "armeb": {
        "user": "qemu-armeb-static",
        "system": "qemu-system-arm",
        "cpu": "ARMEB",
    },
    "aarch64": {
        "user": "qemu-aarch64-static",
        "system": "qemu-system-aarch64",
        "cpu": "AARCH64",
    },
    "aarch64_be": {
        "user": "qemu-aarch64_be-static",
        "system": "qemu-system-aarch64",
        "cpu": "AARCH64_BE",
    },
    "i386": {
        "user": "qemu-i386-static",
        "system": "qemu-system-i386",
        "cpu": "I386",
    },
    "x86_64": {
        "user": "qemu-x86_64-static",
        "system": "qemu-system-x86_64",
        "cpu": "X86_64",
    },
    "ppc": {
        "user": "qemu-ppc-static",
        "system": "qemu-system-ppc",
        "cpu": "PPC",
    },
    "sparc": {
        "user": "qemu-sparc-static",
        "system": "qemu-system-sparc",
        "cpu": "SPARC",
    },
}


@dataclass
class Config:
    """Global configuration for the Emulation Agent."""

    # Network
    agent_host: str = "0.0.0.0"
    agent_port: int = 9100

    # Paths
    rootfs_dir: str = "/tmp/emulation_agent/rootfs"
    logs_dir: str = "/tmp/emulation_agent/logs"
    nvram_templates_dir: str = ""  # auto-resolved relative to package

    # QEMU
    qemu_timeout_default: int = 30
    qemu_timeout_max: int = 300
    qemu_search_paths: list = field(default_factory=lambda: [
        "/usr/bin",
        "/usr/local/bin",
        "/opt/qemu/bin",
    ])

    # Service management
    port_range_start: int = 9000
    port_range_end: int = 9999
    max_services_per_rootfs: int = 10

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Misc
    max_upload_size_mb: int = 512
    cleanup_on_shutdown: bool = True

    # Runtime state (populated at startup)
    available_qemu: dict = field(default_factory=dict)  # arch -> {"user": path, "system": path}
    used_ports: set = field(default_factory=set)

    def resolve_paths(self):
        """Resolve relative paths to absolute."""
        self.rootfs_dir = os.path.abspath(self.rootfs_dir)
        self.logs_dir = os.path.abspath(self.logs_dir)
        if not self.nvram_templates_dir:
            self.nvram_templates_dir = os.path.join(
                os.path.dirname(__file__), "nvram_templates"
            )
        os.makedirs(self.rootfs_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

    def detect_qemu_binaries(self) -> dict:
        """Scan for available QEMU binaries on the system.

        Returns:
            dict: arch -> {"user": "/path/to/qemu-arch-static", "system": "/path/to/qemu-system-arch"}
        """
        available = {}
        for arch, patterns in SUPPORTED_ARCHES.items():
            arch_bins = {}
            for search_path in self.qemu_search_paths:
                if not os.path.isdir(search_path):
                    continue
                # Check user-mode
                user_path = os.path.join(search_path, patterns["user"])
                if os.path.isfile(user_path) and os.access(user_path, os.X_OK):
                    arch_bins["user"] = user_path
                # Check system-mode
                sys_path = os.path.join(search_path, patterns["system"])
                if os.path.isfile(sys_path) and os.access(sys_path, os.X_OK):
                    arch_bins["system"] = sys_path
            if arch_bins:
                available[arch] = arch_bins

        self.available_qemu = available
        logger.info(f"Detected QEMU binaries for: {list(available.keys())}")
        return available

    def get_qemu_user_binary(self, arch: str) -> Optional[str]:
        """Get path to qemu-user-static binary for given arch."""
        info = self.available_qemu.get(arch, {})
        return info.get("user")

    def get_qemu_system_binary(self, arch: str) -> Optional[str]:
        """Get path to qemu-system binary for given arch."""
        info = self.available_qemu.get(arch, {})
        return info.get("system")

    def allocate_port(self) -> int:
        """Allocate a free port from the configured range."""
        import socket
        for port in range(self.port_range_start, self.port_range_end + 1):
            if port in self.used_ports:
                continue
            # Check if actually free
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    self.used_ports.add(port)
                    return port
                except OSError:
                    continue
        raise RuntimeError(
            f"No free ports in range {self.port_range_start}-{self.port_range_end}"
        )

    def release_port(self, port: int):
        """Release a previously allocated port."""
        self.used_ports.discard(port)


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from file and environment.

    Priority: env vars > config file > defaults
    """
    config = Config()

    # Try to load from file
    search_paths = [Path(config_path)] if config_path else DEFAULT_CONFIG_PATHS
    for path in search_paths:
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            if "emulation" in data:
                data = data["emulation"]
            # Map YAML keys to config fields
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            logger.info(f"Loaded config from {path}")
            break

    # Environment variable overrides
    env_mapping = {
        "EMU_AGENT_HOST": "agent_host",
        "EMU_AGENT_PORT": ("agent_port", int),
        "EMU_ROOTFS_DIR": "rootfs_dir",
        "EMU_LOGS_DIR": "logs_dir",
        "EMU_MAX_TIMEOUT": ("qemu_timeout_max", int),
        "EMU_LOG_LEVEL": "log_level",
        "EMU_MAX_UPLOAD_MB": ("max_upload_size_mb", int),
    }
    for env_var, field in env_mapping.items():
        value = os.environ.get(env_var)
        if value is not None:
            if isinstance(field, tuple):
                field_name, converter = field
                setattr(config, field_name, converter(value))
            else:
                setattr(config, field, value)

    # Resolve and create directories
    config.resolve_paths()

    return config


# Singleton instance (initialized at server startup)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global Config singleton."""
    global _config
    if _config is None:
        _config = load_config()
        _config.detect_qemu_binaries()
    return _config


def set_config(config: Config):
    """Set the global Config singleton (for testing)."""
    global _config
    _config = config
