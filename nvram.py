"""
NVRAM Emulator — device-specific NVRAM/config emulation for firmware services.

Many embedded devices (especially routers) store configuration in NVRAM/flash
partitions. Services like GoAhead webserver, boa, and various proprietary httpd
implementations read from /dev/mtd* or NVRAM libraries (libnvram.so) at startup.
Without these values, they crash with segfault or hang.

This module provides:
- Auto-detection of device type from rootfs contents
- Template-based NVRAM config generation for common devices
- Manual key-value configuration
- Integration with QEMU -E (environment) and LD_PRELOAD hooks
"""

import os
import re
import json
import shutil
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Well-known NVRAM values by device type.
# These are the minimum set needed to get common services running.

NVRAM_TEMPLATES: Dict[str, Dict[str, str]] = {
    # ------------------------------------------------------------------
    # GoAhead webserver (used in many D-Link, Tenda, Netgear, TP-Link)
    # ------------------------------------------------------------------
    "goahead": {
        "http_username": "admin",
        "http_password": "admin",
        "http_lanport": "80",
        "http_wanport": "8080",
        "lan_ipaddr": "192.168.0.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "dhcp_start": "192.168.0.100",
        "dhcp_end": "192.168.0.200",
        "dhcp_lease": "86400",
        "wlan_ssid": "Wireless",
        "wlan_mode": "11bgn",
        "wlan_channel": "6",
        "wlan_security_mode": "wpapsk",
        "wlan_wpa_psk": "password",
        "router_name": "Router",
        "firmware_version": "1.0.0",
        "hardware_version": "A1",
        "default_language": "EN",
        "time_zone": "GMT",
    },

    # ------------------------------------------------------------------
    # D-Link specific (often uses GoAhead + D-Link extensions)
    # ------------------------------------------------------------------
    "dlink": {
        "http_username": "admin",
        "http_password": "admin",
        "lan_ipaddr": "192.168.0.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "dhcp_start": "192.168.0.100",
        "dhcp_end": "192.168.0.199",
        "wireless_ssid": "dlink",
        "wireless_mode": "mixed",
        "wireless_channel": "6",
        "wireless_security": "wpa2",
        "wireless_password": "",
        "admin_password": "admin",
        "user_password": "user",
        "remote_management": "0",
        "remote_port": "8080",
        "upnp_enable": "1",
        "wan_mode": "dhcp",
        "pppoe_username": "",
        "pppoe_password": "",
        "ddns_enable": "0",
        "log_enable": "1",
        "time_zone": "8",
        "ntp_server": "pool.ntp.org",
        "firmware_version": "1.00",
        "hardware_revision": "A1",
        "device_name": "D-Link Router",
        "model_name": "DIR-XXX",
    },

    # ------------------------------------------------------------------
    # Tenda specific
    # ------------------------------------------------------------------
    "tenda": {
        "lan_ipaddr": "192.168.0.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "dhcp_start": "192.168.0.100",
        "dhcp_end": "192.168.0.200",
        "http_username": "admin",
        "http_password": "admin",
        "wl_ssid": "Tenda",
        "wl_mode": "11bgn",
        "wl_channel": "6",
        "wl_encryption": "wpa2",
        "wl_wpa_psk": "",
        "remote_enable": "0",
        "upnp_enable": "1",
        "time_zone": "8",
        "router_name": "Tenda",
        "fw_version": "1.0",
    },

    # ------------------------------------------------------------------
    # Netgear specific
    # ------------------------------------------------------------------
    "netgear": {
        "lan_ipaddr": "192.168.1.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "dhcp_start": "192.168.1.2",
        "dhcp_end": "192.168.1.254",
        "http_username": "admin",
        "http_password": "password",
        "board_id": "U12H",
        "region": "WW",
        "wlan_ssid": "NETGEAR",
        "wlan_passphrase": "",
        "wlan_security": "wpa2",
        "upnp_enable": "1",
        "remote_mgmt": "0",
        "firmware_version": "1.0.0",
        "model_number": "RXXXX",
    },

    # ------------------------------------------------------------------
    # TP-Link specific
    # ------------------------------------------------------------------
    "tplink": {
        "lan_ipaddr": "192.168.0.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "http_username": "admin",
        "http_password": "admin",
        "ssid": "TP-LINK",
        "wireless_key": "",
        "wireless_mode": "11bgn",
        "wireless_security": "wpa2",
        "dhcp_enable": "1",
        "upnp_enable": "1",
        "remote_mgmt": "0",
        "time_zone": "8",
        "firmware_version": "1.0.0",
        "hardware_version": "v1",
    },

    # ------------------------------------------------------------------
    # Huawei/H3C specific
    # ------------------------------------------------------------------
    "huawei": {
        "lan_ipaddr": "192.168.1.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "http_username": "admin",
        "http_password": "admin",
        "web_username": "admin",
        "web_password": "admin",
        "wl_ssid": "HUAWEI",
        "wl_wpa_psk": "",
        "dhcp_enable": "1",
    },

    # ------------------------------------------------------------------
    # Generic / minimal — safe defaults when we can't identify the device
    # ------------------------------------------------------------------
    "generic": {
        "lan_ipaddr": "192.168.0.1",
        "lan_netmask": "255.255.255.0",
        "wan_ipaddr": "0.0.0.0",
        "wan_netmask": "0.0.0.0",
        "http_username": "admin",
        "http_password": "admin",
        "dhcp_start": "192.168.0.100",
        "dhcp_end": "192.168.0.200",
        "wlan_ssid": "Router",
        "fw_version": "1.0.0",
        "time_zone": "GMT",
    },
}


class NvramEmulator:
    """Manages NVRAM configuration for emulated firmware."""

    def __init__(self, templates_dir: Optional[str] = None):
        self.templates_dir = templates_dir or os.path.join(
            os.path.dirname(__file__), "nvram_templates"
        )
        self.templates: Dict[str, Dict[str, str]] = dict(NVRAM_TEMPLATES)

        # Load custom templates
        self._load_custom_templates()

    def _load_custom_templates(self):
        """Load additional NVRAM templates from templates directory."""
        if not os.path.isdir(self.templates_dir):
            return
        for fname in os.listdir(self.templates_dir):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(self.templates_dir, fname)) as f:
                        template = json.load(f)
                    name = fname.replace(".json", "")
                    if isinstance(template, dict):
                        self.templates[name] = template
                        logger.debug(f"Loaded NVRAM template: {name}")
                except Exception as e:
                    logger.warning(f"Failed to load template {fname}: {e}")

    # ------------------------------------------------------------------
    # Device detection
    # ------------------------------------------------------------------

    def detect_device_type(self, rootfs_path: str) -> str:
        """Auto-detect device type from rootfs contents.

        Returns one of the known template names or 'generic'.
        """
        rootfs = os.path.abspath(rootfs_path)
        scores: Dict[str, int] = {}

        # Check for vendor-specific files
        vendor_indicators = {
            "dlink": [
                r"etc_ro/dlink", r"etc/dlink", r"bin/dlink",
                r"etc/def_default/.*dlink", r"www/goform", r"www/dir",
            ],
            "tenda": [
                r"etc_ro/tenda", r"etc/tenda", r"bin/tenda",
                r"web/tenda", r"etc/Wireless/RT2860AP",
                r"etc_ro/defaults/.*Tenda",
            ],
            "netgear": [
                r"etc/netgear", r"etc/def_default/.*netgear",
                r"bin/netgear", r"usr/sbin/net-scan",
            ],
            "tplink": [
                r"etc/tp-link", r"etc/tplink", r"bin/tplink",
                r"etc/default/.*tplink", r"web/tp-link",
            ],
            "huawei": [
                r"etc/huawei", r"etc/hw", r"bin/huawei",
                r"etc/default/.*huawei", r"web/huawei",
            ],
            "goahead": [
                r"bin/goahead", r"usr/sbin/goahead",
                r"etc/goahead", r"etc_ro/goahead",
                r"lib/libgoahead", r"www/goahead",
            ],
        }

        for vendor, patterns in vendor_indicators.items():
            for pattern in patterns:
                try:
                    result = list(Path(rootfs).rglob(pattern.lstrip("/").replace("/", os.sep)))
                    if result:
                        scores[vendor] = scores.get(vendor, 0) + len(result)
                except Exception:
                    pass

        # Check binary names
        bin_dirs = ["bin", "sbin", "usr/bin", "usr/sbin"]
        for d in bin_dirs:
            bin_path = os.path.join(rootfs, d)
            if not os.path.isdir(bin_path):
                continue
            for fname in os.listdir(bin_path):
                fname_lower = fname.lower()
                for vendor, keywords in [
                    ("dlink", ["dlink", "alphapd", "dns_relay"]),
                    ("tenda", ["tenda", "cfmd"]),
                    ("netgear", ["netgear", "net-genie", "genie"]),
                    ("tplink", ["tplink", "tp-link"]),
                    ("huawei", ["huawei", "mini_httpd"]),
                    ("goahead", ["goahead", "webs"]),
                ]:
                    for kw in keywords:
                        if kw in fname_lower:
                            scores[vendor] = scores.get(vendor, 0) + 2

        # Check strings in httpd binaries for vendor names
        for bin_dir in bin_dirs:
            d = os.path.join(rootfs, bin_dir)
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                if not any(n in fname.lower() for n in ["httpd", "webs", "goahead", "boa"]):
                    continue
                fpath = os.path.join(d, fname)
                if os.path.getsize(fpath) > 10 * 1024 * 1024:  # Skip huge files
                    continue
                try:
                    with open(fpath, "rb") as f:
                        content = f.read(1024 * 1024)
                    # Quick grep for vendor names
                    text = content.decode("latin-1", errors="ignore").lower()
                    for vendor, keywords in [
                        ("dlink", ["d-link", "dlink"]),
                        ("tenda", ["tenda"]),
                        ("netgear", ["netgear"]),
                        ("tplink", ["tp-link", "tplink"]),
                        ("huawei", ["huawei"]),
                    ]:
                        for kw in keywords:
                            if kw in text:
                                scores[vendor] = scores.get(vendor, 0) + 5
                except Exception:
                    pass

        if not scores:
            logger.info(f"No vendor detected for {rootfs_path}, using 'generic'")
            return "generic"

        best = max(scores, key=scores.get)
        logger.info(
            f"Device detection for {os.path.basename(rootfs_path)}: "
            f"best={best}, scores={dict(sorted(scores.items(), key=lambda x: -x[1])[:5])}"
        )

        # If goahead is detected with another vendor, use the vendor
        if best == "goahead" and len(scores) > 1:
            second = sorted(scores.items(), key=lambda x: -x[1])[1]
            if second[1] >= scores["goahead"] * 0.5:
                best = second[0]

        return best

    # ------------------------------------------------------------------
    # Configuration generation
    # ------------------------------------------------------------------

    def generate_config(
        self,
        rootfs_path: str,
        device_type: str = "auto",
        overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Generate NVRAM configuration for a rootfs.

        Args:
            rootfs_path: Path to extracted rootfs.
            device_type: Device type name or 'auto' for auto-detection.
            overrides: Additional key-value pairs to set/override.

        Returns:
            Dict of NVRAM key-value pairs that were set.
        """
        if device_type == "auto":
            device_type = self.detect_device_type(rootfs_path)

        template = self.templates.get(device_type, self.templates["generic"]).copy()
        if overrides:
            template.update(overrides)

        logger.info(
            f"Generated {len(template)} NVRAM values for {device_type} "
            f"({os.path.basename(rootfs_path)})"
        )
        return template

    # ------------------------------------------------------------------
    # File-based NVRAM
    # ------------------------------------------------------------------

    def write_nvram_config(
        self,
        rootfs_path: str,
        device_type: str = "auto",
        overrides: Optional[Dict[str, str]] = None,
    ) -> str:
        """Write NVRAM configuration files into the rootfs.

        Creates:
        - etc_ro/nvram.conf  (GoAhead-style)
        - etc/nvram.conf     (alternative location)
        - etc/default.cfg    (some D-Link)
        - tmp/nvram          (some Tenda)

        Args:
            rootfs_path: Path to rootfs directory.
            device_type: Device type or 'auto'.
            overrides: Additional config values.

        Returns:
            Path to the primary config file written.
        """
        config = self.generate_config(rootfs_path, device_type, overrides)

        # Format config as key=value pairs
        lines = []
        for key, value in sorted(config.items()):
            lines.append(f"{key}={value}")

        config_content = "\n".join(lines) + "\n"

        # Write to multiple expected locations
        locations = [
            os.path.join(rootfs_path, "etc_ro"),
            os.path.join(rootfs_path, "etc"),
            os.path.join(rootfs_path, "tmp"),
        ]

        primary_path = None
        for loc in locations:
            os.makedirs(loc, exist_ok=True)

            # GoAhead-style nvram.conf
            if "etc_ro" in loc or "etc" in loc:
                conf_path = os.path.join(loc, "nvram.conf")
                with open(conf_path, "w") as f:
                    f.write(config_content)
                if primary_path is None:
                    primary_path = conf_path
                logger.debug(f"Wrote NVRAM config to {conf_path}")

            # Some devices use default.cfg
            if "etc" in loc:
                cfg_path = os.path.join(loc, "default.cfg")
                with open(cfg_path, "w") as f:
                    f.write(config_content)

            # Tenda-style
            if "tmp" in loc:
                nvram_path = os.path.join(loc, "nvram")
                with open(nvram_path, "w") as f:
                    f.write(config_content)

        return primary_path or ""

    # ------------------------------------------------------------------
    # Environment-based NVRAM (for QEMU -E)
    # ------------------------------------------------------------------

    def get_qemu_env(self, rootfs_path: str, device_type: str = "auto") -> Dict[str, str]:
        """Generate environment variables for use with QEMU -E flag.

        Some services read NVRAM values from environment variables
        rather than files. This provides those as a dict.
        """
        config = self.generate_config(rootfs_path, device_type)
        # Uppercase the keys as many services expect them as env vars
        env = {}
        for key, value in config.items():
            env[key.upper()] = value
            env[key] = value  # Also provide lowercase variant
        return env

    # ------------------------------------------------------------------
    # Individual NVRAM operations
    # ------------------------------------------------------------------

    def get_value(self, key: str, rootfs_path: str) -> Optional[str]:
        """Read an NVRAM value from the rootfs config files."""
        search_paths = [
            os.path.join(rootfs_path, "etc_ro", "nvram.conf"),
            os.path.join(rootfs_path, "etc", "nvram.conf"),
            os.path.join(rootfs_path, "tmp", "nvram"),
        ]
        for path in search_paths:
            if os.path.isfile(path):
                try:
                    with open(path) as f:
                        for line in f:
                            line = line.strip()
                            if "=" in line:
                                k, v = line.split("=", 1)
                                if k.strip() == key:
                                    return v.strip()
                except Exception:
                    pass
        return None

    def set_value(self, key: str, value: str, rootfs_path: str) -> bool:
        """Set or update a single NVRAM value in existing config files."""
        search_paths = [
            os.path.join(rootfs_path, "etc_ro", "nvram.conf"),
            os.path.join(rootfs_path, "etc", "nvram.conf"),
        ]
        updated = False
        for path in search_paths:
            if os.path.isfile(path):
                try:
                    with open(path) as f:
                        lines = f.readlines()
                    found = False
                    for i, line in enumerate(lines):
                        if line.strip().startswith(f"{key}="):
                            lines[i] = f"{key}={value}\n"
                            found = True
                            break
                    if not found:
                        lines.append(f"{key}={value}\n")
                    with open(path, "w") as f:
                        f.writelines(lines)
                    updated = True
                except Exception as e:
                    logger.warning(f"Failed to update {path}: {e}")

        if not updated:
            # Create the file
            conf_path = os.path.join(rootfs_path, "etc_ro", "nvram.conf")
            os.makedirs(os.path.dirname(conf_path), exist_ok=True)
            with open(conf_path, "w") as f:
                f.write(f"{key}={value}\n")

        return True

    def list_templates(self) -> List[str]:
        """List available device templates."""
        return sorted(self.templates.keys())
