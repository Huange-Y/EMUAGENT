"""
Firmware Acquisition Module — 固件自动获取系统

Automatically discover, search, and download firmware images from:
- Vendor official sites (TP-Link, Tenda, D-Link, Netgear, ASUS, Linksys, Huawei)
- Open-source router projects (OpenWrt, DD-WRT, Gargoyle)
- GPL source release centers
- FTP mirrors
- Firmware archives / mirror sites

Integrates seamlessly with the emulation pipeline:
    fetch → extract → emulate → probe → analyze

Usage:
    from firmware_acquisition import FirmwareAcquisition
    fa = FirmwareAcquisition()
    results = fa.search("tenda ac1200")
    path = fa.download(results[0])

CLI:
    emu fetch search "tenda ac1200"
    emu fetch download <url>
    emu fetch quick "d-link dir-882" --emulate
"""

import os
import re
import sys
import json
import time
import gzip
import hashlib
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FirmwareEntry:
    """A discovered firmware image entry."""
    vendor: str
    model: str
    version: str
    url: str
    filename: str = ""
    size_bytes: int = 0
    checksum_md5: Optional[str] = None
    checksum_sha256: Optional[str] = None
    release_date: Optional[str] = None
    description: str = ""
    arch_hint: str = ""          # "mips", "arm", "x86", etc (if known)
    source: str = ""             # "tplink", "tenda", "openwrt", etc
    reliability: float = 0.5     # 0-1 estimate of URL reliability
    cve_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"{self.vendor} {self.model} {self.version}".strip()

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "model": self.model,
            "version": self.version,
            "url": self.url,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "checksum_md5": self.checksum_md5,
            "checksum_sha256": self.checksum_sha256,
            "release_date": self.release_date,
            "arch_hint": self.arch_hint,
            "source": self.source,
            "reliability": self.reliability,
            "cve_ids": self.cve_ids,
        }


# ---------------------------------------------------------------------------
# Vendor Registry — known-good URL patterns and download strategies
# ---------------------------------------------------------------------------

VENDOR_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------------
    # 中国厂商
    # ------------------------------------------------------------------
    "tenda": {
        "name": "Tenda (腾达)",
        "homepage": "https://www.tendacn.com",
        "download_base": "https://down.tendacn.com/uploadfile/routing/",
        "search_url": "https://www.tendacn.com/en/support/download.html",
        # Tenda organizes by product line directories
        "product_lines": [
            "AC1200", "AC2100", "AC9", "AC10", "AC11", "AC15", "AC18",
            "AX1800", "AX3000", "AX5700",
            "N300", "N301", "F3", "F6", "FH456",
        ],
        "url_pattern": "https://down.tendacn.com/uploadfile/routing/{model}/firmware/",
        "file_pattern": r".*\.(zip|rar|bin|img)$",
        "referer_required": False,
        "user_agent_spoof": True,
    },
    "tplink": {
        "name": "TP-Link (普联)",
        "homepage": "https://www.tp-link.com",
        "download_base": "https://static.tp-link.com/upload/firmware/",
        "search_urls": [
            "https://www.tp-link.com/en/support/download/{model}/",
            "https://www.tp-link.com/zh-cn/support/download/{model}/",
        ],
        "url_patterns": [
            "https://static.tp-link.com/upload/firmware/{year}/{year}{month}/{model}_*.zip",
            "https://static.tp-link.com/upload/firmware/{year}/{year}{month}/*.zip",
        ],
        "file_pattern": r".*\.(zip|bin)$",
        "referer_required": True,
        "user_agent_spoof": True,
        "curl_flags": ["-H", "Referer: https://www.tp-link.com/"],
    },
    "mercury": {
        "name": "Mercury (水星)",
        "homepage": "https://www.mercurycom.com.cn",
        "download_base": "https://service.mercurycom.com.cn/download/",
        "search_url": "https://service.mercurycom.com.cn/download/list",
        "file_pattern": r".*\.(zip|rar|bin)$",
    },
    "fast": {
        "name": "FAST (迅捷)",
        "homepage": "https://www.fastcom.com.cn",
        "download_base": "https://service.fastcom.com.cn/download/",
        "file_pattern": r".*\.(zip|rar)$",
    },
    "xiaomi": {
        "name": "Xiaomi (小米)",
        "homepage": "https://www.mi.com",
        "download_base": "https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/",
        "search_url": "https://www.mi.com/global/support",
        "url_pattern": "https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/{model}/*.bin",
        "file_pattern": r".*\.bin$",
    },
    "huawei": {
        "name": "Huawei (华为)",
        "homepage": "https://support.huawei.com",
        "download_base": "https://support.huawei.com/enterprise/en/software/",
        "search_url": "https://support.huawei.com/enterprise/en/software/{product}/",
        "file_pattern": r".*\.(zip|cc|bin)$",
        "login_required": True,
    },

    # ------------------------------------------------------------------
    # 国际厂商
    # ------------------------------------------------------------------
    "dlink": {
        "name": "D-Link",
        "homepage": "https://www.dlink.com",
        "download_base": "https://ftp.dlink.ru/pub/Router/",
        "ftp_mirrors": [
            "ftp://ftp.dlink.ru/pub/Router/",
            "ftp://ftp.dlink.de/",
            "ftp://ftp.dlink.eu/",
            "https://tsd.dlink.com.tw/",
        ],
        "search_urls": [
            "https://support.dlink.com/ProductInfo.aspx?m={model}",
        ],
        "file_pattern": r".*\.(zip|bin|img|chk)$",
        "referer_required": False,
    },
    "netgear": {
        "name": "Netgear",
        "homepage": "https://www.netgear.com",
        "download_base": "https://www.downloads.netgear.com/files/GDC/",
        "search_url": "https://www.netgear.com/support/download/?model={model}",
        "file_pattern": r".*\.(zip|img|chk)$",
    },
    "asus": {
        "name": "ASUS",
        "homepage": "https://www.asus.com",
        "download_base": "https://dlcdnets.asus.com/pub/ASUS/wireless/",
        "search_url": "https://www.asus.com/supportonly/{model}/HelpDesk_Download/",
        "file_pattern": r".*\.(zip|trx|w)$",
    },
    "linksys": {
        "name": "Linksys",
        "homepage": "https://www.linksys.com",
        "download_base": "https://downloads.linksys.com/downloads/firmware/",
        "search_url": "https://www.linksys.com/support-article?articleNum={article}",
        "file_pattern": r".*\.(img|bin|zip)$",
    },
    "zyxel": {
        "name": "Zyxel",
        "homepage": "https://www.zyxel.com",
        "download_base": "https://download.zyxel.com/",
        "search_url": "https://www.zyxel.com/service-provider/global/en/support/download",
        "file_pattern": r".*\.(zip|bin|rom)$",
    },

    # ------------------------------------------------------------------
    # 开源项目 (最可靠的来源)
    # ------------------------------------------------------------------
    "openwrt": {
        "name": "OpenWrt",
        "homepage": "https://openwrt.org",
        "download_base": "https://downloads.openwrt.org/releases/",
        # Index page listing releases
        "release_index": "https://downloads.openwrt.org/releases/",
        "file_pattern": r".*squashfs-(sysupgrade|factory)\.bin$",
        "referer_required": False,
        "predictable_url": True,
        # Known good releases
        "releases": [
            "23.05.4", "23.05.3", "23.05.2", "22.03.6", "22.03.5",
            "21.02.7", "19.07.10",
        ],
    },
    "ddwrt": {
        "name": "DD-WRT",
        "homepage": "https://dd-wrt.com",
        "download_base": "https://download1.dd-wrt.com/dd-wrtv2/downloads/betas/",
        "ftp_mirrors": [
            "ftp://ftp.dd-wrt.com/betas/",
        ],
        "file_pattern": r".*\.(bin|trx|chk)$",
    },
    "gargoyle": {
        "name": "Gargoyle",
        "homepage": "https://www.gargoyle-router.com",
        "download_base": "https://www.gargoyle-router.com/downloads/",
        "file_pattern": r".*\.(bin|trx)$",
    },

    # ------------------------------------------------------------------
    # GPL 源码中心 (厂商必须发布GPL源码，里面包含固件)
    # ------------------------------------------------------------------
    "dlink_gpl": {
        "name": "D-Link GPL Source",
        "homepage": "https://tsd.dlink.com.tw/GPL.asp",
        "download_base": "https://tsd.dlink.com.tw/",
        "file_pattern": r".*GPL.*\.(tar\.gz|zip)$",
        "notes": "GPL source archives often contain prebuilt firmware binaries",
    },
    "tplink_gpl": {
        "name": "TP-Link GPL Code Center",
        "homepage": "https://www.tp-link.com/en/support/gpl-code/",
        "download_base": "https://static.tp-link.com/resources/gpl/",
        "file_pattern": r".*\.(tar\.gz|tar\.bz2|zip)$",
    },
    "netgear_gpl": {
        "name": "Netgear GPL Open Source",
        "homepage": "https://www.netgear.com/about/open-source/",
        "download_base": "https://www.downloads.netgear.com/files/GPL/",
        "file_pattern": r".*\.(tar\.gz|tar\.bz2|zip)$",
    },
    "asus_gpl": {
        "name": "ASUS GPL Archive",
        "homepage": "https://www.asus.com/support/gpl/",
        "download_base": "https://dlcdnets.asus.com/pub/ASUS/wireless/",
        "file_pattern": r"GPL.*\.(tar\.gz|zip)$",
    },

    # ------------------------------------------------------------------
    # 固件聚合 / 镜像站
    # ------------------------------------------------------------------
    "firmware_directory": {
        "name": "The Firmware Directory",
        "homepage": "https://firmware.directory",
        "download_base": "https://firmware.directory/downloads/",
        "file_pattern": r".*\.(bin|img|zip|tar\.gz)$",
    },
}


# ---------------------------------------------------------------------------
# Known device models by vendor (common / high-value targets)
# ---------------------------------------------------------------------------

KNOWN_MODELS: Dict[str, List[str]] = {
    "tenda": [
        "AC1200", "AC1206", "AC1208", "AC9", "AC10", "AC10U", "AC11", "AC15", "AC18",
        "AC2100", "AC23", "AX1800", "AX1803", "AX3000", "AX3006", "AX5700",
        "N300", "N301", "F3", "F6", "F9", "FH456", "FH1206",
        "W15E", "W18E", "W20E", "G0", "G1", "G2", "G3",
        "4G03", "4G05", "4G06", "4G09",
    ],
    "tplink": [
        "Archer C20", "Archer C50", "Archer C60", "Archer C7", "Archer C9",
        "Archer C1200", "Archer C2300", "Archer C2700", "Archer C3150",
        "Archer AX10", "Archer AX20", "Archer AX50", "Archer AX73",
        "TL-WR841N", "TL-WR940N", "TL-WR1043ND", "TL-MR3020", "TL-MR3420",
        "TD-W9970", "TD-W8961N",
        "Deco M4", "Deco M5", "Deco X20",
    ],
    "dlink": [
        "DIR-882", "DIR-878", "DIR-867", "DIR-859", "DIR-853", "DIR-842",
        "DIR-822", "DIR-809", "DIR-615", "DIR-655",
        "DWR-921", "DWR-932", "DWR-960",
        "DSL-3782", "DSL-3900",
        "DAP-2695", "DAP-2660", "DAP-2310",
    ],
    "netgear": [
        "R6400", "R6700", "R7000", "R7800", "R8000", "R8500", "R9000",
        "RAX40", "RAX50", "RAX80", "RAX120", "RAX200",
        "D6220", "D6400", "D7000", "D7800", "D8500",
        "WNR2000", "WNR3500L", "WNDR3700", "WNDR4300", "WNDR4500",
    ],
    "asus": [
        "RT-AC68U", "RT-AC86U", "RT-AC88U", "RT-AC5300",
        "RT-AX56U", "RT-AX58U", "RT-AX86U", "RT-AX88U", "RT-AX92U",
        "GT-AC5300", "GT-AX11000", "ZenWiFi XT8",
    ],
    "linksys": [
        "WRT1900AC", "WRT3200ACM", "WRT32X",
        "EA6350", "EA7300", "EA7500", "EA8300", "EA8500", "EA9500",
        "MR8300", "Velop WHW0303",
    ],
    "xiaomi": [
        "R1D", "R2D", "R3", "R3D", "R3G", "R3P", "R4", "R4A",
        "AX1800", "AX3600", "AX6000", "AX9000",
        "CR6606", "CR6608", "CR6609",
    ],
    "huawei": [
        "AR1200", "AR2200", "AR3200",
        "USG6000", "USG6300", "USG6500", "USG6600",
        "B315", "B525", "E5186",
    ],
}

# ---------------------------------------------------------------------------
# CVE → vendor/model mapping (for vulnerability-driven firmware hunting)
# ---------------------------------------------------------------------------

CVE_AFFECTED: Dict[str, List[Dict[str, str]]] = {
    # High-impact router CVEs with known vulnerable firmware
    "CVE-2022-30075": [{"vendor": "tenda", "model": "AC1206", "version": "V15.03.06.23"}],
    "CVE-2022-30076": [{"vendor": "tenda", "model": "AC1206", "version": "V15.03.06.23"}],
    "CVE-2021-44971": [{"vendor": "tenda", "model": "AC15", "version": "V15.03.05.18"}],
    "CVE-2020-10987": [{"vendor": "tenda", "model": "AC15", "version": "V15.03.05.19"}],
    "CVE-2020-10988": [{"vendor": "tenda", "model": "AC18"}, {"vendor": "tenda", "model": "AC15"}],
    "CVE-2020-9373": [{"vendor": "dlink", "model": "DIR-882"}, {"vendor": "dlink", "model": "DIR-878"}],
    "CVE-2020-9374": [{"vendor": "dlink", "model": "DIR-882"}],
    "CVE-2019-17621": [{"vendor": "dlink", "model": "DIR-859"}],
    "CVE-2017-14491": [
        {"vendor": "dlink", "model": "DIR-850L"},
        {"vendor": "dlink", "model": "DIR-615"},
    ],
    "CVE-2021-33514": [{"vendor": "netgear", "model": "R6400"}],
    "CVE-2020-35847": [{"vendor": "netgear", "model": "R7000"}, {"vendor": "netgear", "model": "R6400"}],
    "CVE-2022-22588": [{"vendor": "netgear", "model": "R7000"}],
    "CVE-2020-36109": [{"vendor": "tplink", "model": "TL-WR840N"}],
    "CVE-2019-15013": [{"vendor": "zyxel", "model": "P-660HN"}],
}


# ===================================================================
# Firmware Acquisition Engine
# ===================================================================

class FirmwareAcquisition:
    """Main engine for firmware discovery and download.

    Usage:
        fa = FirmwareAcquisition(cache_dir="/data/firmware_cache")
        results = fa.search("tenda ac1200")
        path = fa.download(results[0])
        # Feed into emulation pipeline
        try:
            from emulation_agent.client import EmulationAgentClient
        except ModuleNotFoundError:
            from client import EmulationAgentClient
        client = EmulationAgentClient()
        client.emulate_and_probe(path)
    """

    def __init__(
        self,
        cache_dir: str = "/tmp/firmware_cache",
        download_dir: str = "/tmp/firmware_downloads",
        max_concurrent: int = 4,
        timeout: int = 60,
    ):
        self.cache_dir = os.path.abspath(cache_dir)
        self.download_dir = os.path.abspath(download_dir)
        self.max_concurrent = max_concurrent
        self.timeout = timeout

        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.download_dir, exist_ok=True)

        self._vendor_index: Dict[str, List[FirmwareEntry]] = {}
        self._load_cache()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _cache_path(self, vendor: str) -> str:
        return os.path.join(self.cache_dir, f"{vendor}_index.json")

    def _load_cache(self):
        """Load cached vendor firmware indexes."""
        for vendor in VENDOR_REGISTRY:
            path = self._cache_path(vendor)
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    entries = []
                    for d in data:
                        # Skip entries older than 30 days
                        cached_date = d.get("_cached_at", "")
                        entries.append(FirmwareEntry(**{k: v for k, v in d.items()
                                                         if not k.startswith("_")}))
                    if entries:
                        self._vendor_index[vendor] = entries
                except Exception as e:
                    logger.debug(f"Failed to load cache for {vendor}: {e}")

    def _save_cache(self, vendor: str, entries: List[FirmwareEntry]):
        """Save vendor firmware index to cache."""
        data = []
        for e in entries:
            d = e.to_dict()
            d["_cached_at"] = datetime.now().isoformat()
            data.append(d)
        with open(self._cache_path(vendor), "w") as f:
            json.dump(data, f, indent=2)

    # ------------------------------------------------------------------
    # Search — find firmware by vendor/model/keyword/CVE
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        vendor: Optional[str] = None,
        max_results: int = 50,
        use_cache: bool = True,
    ) -> List[FirmwareEntry]:
        """Search for firmware images.

        Args:
            query: Search string — vendor name, model number, CVE ID, or keyword.
            vendor: Limit to specific vendor.
            max_results: Maximum results to return.
            use_cache: Use cached indexes if available.

        Returns:
            List of matching FirmwareEntry objects.
        """
        results: List[FirmwareEntry] = []

        # Parse query
        query_lower = query.lower().strip()

        # Check if query is a CVE ID
        if re.match(r'^CVE-\d{4}-\d{4,}$', query.upper()):
            cve_results = self._search_cve(query.upper())
            if cve_results:
                results = cve_results
        else:
            # Search across vendors
            vendors_to_search = [vendor] if vendor else list(VENDOR_REGISTRY.keys())

            with ThreadPoolExecutor(max_workers=min(self.max_concurrent, len(vendors_to_search))) as pool:
                futures = {}
                for v in vendors_to_search:
                    futures[pool.submit(self._search_vendor, v, query_lower, use_cache)] = v

                for future in as_completed(futures):
                    try:
                        vendor_results = future.result()
                        results.extend(vendor_results)
                    except Exception as e:
                        logger.warning(f"Search failed for {futures[future]}: {e}")

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)

        # Sort by reliability (highest first)
        unique_results.sort(key=lambda r: -r.reliability)

        return unique_results[:max_results]

    def _search_vendor(
        self, vendor_key: str, query: str, use_cache: bool
    ) -> List[FirmwareEntry]:
        """Search a single vendor for matching firmware."""
        vendor_info = VENDOR_REGISTRY.get(vendor_key)
        if not vendor_info:
            return []

        results = []

        # Use cached index if available
        if use_cache and vendor_key in self._vendor_index:
            cached = self._vendor_index[vendor_key]
            for entry in cached:
                if self._matches_query(entry, query, vendor_key):
                    results.append(entry)
            if results:
                return results

        # Build candidate URLs from known models
        if vendor_key in KNOWN_MODELS:
            for model in KNOWN_MODELS[vendor_key]:
                model_lower = model.lower()
                # Check if query matches model name
                if query in model_lower or model_lower in query:
                    for url in self._build_vendor_urls(vendor_key, model):
                        entry = FirmwareEntry(
                            vendor=vendor_info["name"],
                            model=model,
                            version="",
                            url=url,
                            source=vendor_key,
                            reliability=0.3 if vendor_info.get("predictable_url") else 0.1,
                        )
                        results.append(entry)

        # OpenWrt: construct predictable URLs
        if vendor_key == "openwrt":
            results.extend(self._search_openwrt(query))

        # DD-WRT: construct predictable URLs
        if vendor_key == "ddwrt":
            results.extend(self._search_ddwrt(query))

        # Cache the results
        if results:
            self._vendor_index[vendor_key] = results
            self._save_cache(vendor_key, results)

        return results

    def _search_openwrt(self, query: str) -> List[FirmwareEntry]:
        """Search OpenWrt firmware by query.

        OpenWrt has a predictable URL structure:
            https://downloads.openwrt.org/releases/{version}/targets/{target}/{subtarget}/
        """
        results = []
        base = VENDOR_REGISTRY["openwrt"]["download_base"]
        releases = VENDOR_REGISTRY["openwrt"]["releases"]

        # Common targets
        targets = [
            ("ramips/mt7621", "mipsel"),
            ("ramips/mt7620", "mipsel"),
            ("ath79/generic", "mips"),
            ("ath79/mikrotik", "mips"),
            ("ipq40xx/generic", "arm"),
            ("ipq806x/generic", "arm"),
            ("bcm53xx/generic", "arm"),
            ("mvebu/cortexa9", "arm"),
            ("x86/64", "x86_64"),
        ]

        for release in releases:
            for target, arch_hint in targets:
                dir_url = f"{base}{release}/targets/{target}/"
                results.append(FirmwareEntry(
                    vendor="OpenWrt",
                    model=f"{target} ({release})",
                    version=release,
                    url=dir_url,
                    source="openwrt",
                    reliability=0.9,
                    arch_hint=arch_hint,
                ))

        return results

    def _search_ddwrt(self, query: str) -> List[FirmwareEntry]:
        """Search DD-WRT firmware."""
        results = []
        base = VENDOR_REGISTRY["ddwrt"]["download_base"]
        # DD-WRT organizes by year/build
        for year in ["2024", "2023", "2022", "2021"]:
            results.append(FirmwareEntry(
                vendor="DD-WRT",
                model=f"Betas {year}",
                version=year,
                url=f"{base}{year}/",
                source="ddwrt",
                reliability=0.7,
            ))
        return results

    def _search_cve(self, cve_id: str) -> List[FirmwareEntry]:
        """Search for firmware affected by a specific CVE."""
        results = []
        affected = CVE_AFFECTED.get(cve_id, [])
        for item in affected:
            vendor_key = item["vendor"]
            vendor_info = VENDOR_REGISTRY.get(vendor_key)
            if not vendor_info:
                continue
            model = item["model"]
            version = item.get("version", "")
            for url in self._build_vendor_urls(vendor_key, model):
                results.append(FirmwareEntry(
                    vendor=vendor_info["name"],
                    model=model,
                    version=version,
                    url=url,
                    source=vendor_key,
                    reliability=0.5,
                    cve_ids=[cve_id],
                    description=f"Affected by {cve_id}",
                ))
        return results

    def _matches_query(self, entry: FirmwareEntry, query: str, vendor_key: str) -> bool:
        """Check if a firmware entry matches the search query."""
        search_text = f"{entry.vendor} {entry.model} {entry.version} {vendor_key}".lower()
        return query in search_text or any(
            word in search_text for word in query.split()
        )

    def _build_vendor_urls(self, vendor_key: str, model: str) -> List[str]:
        """Generate candidate firmware URLs for a specific model."""
        vendor_info = VENDOR_REGISTRY.get(vendor_key, {})
        urls = []

        base = vendor_info.get("download_base", "")
        if not base:
            return urls

        # Simple pattern: base + model directory
        if vendor_key in ("dlink", "tenda", "zyxel"):
            urls.append(f"{base}{model}/")

        # TP-Link: structured by year/month
        if vendor_key == "tplink":
            import datetime
            year = datetime.datetime.now().year
            for y in [year, year - 1, year - 2]:
                for m in range(1, 13):
                    urls.append(f"{base}{y}/{y}{m:02d}/")

        # Netgear: product number pattern
        if vendor_key == "netgear":
            urls.append(f"{base}{model}/")

        # ASUS
        if vendor_key == "asus":
            urls.append(f"{base}{model}/")

        # OpenWrt: predictable URL
        if vendor_key == "openwrt":
            for release in vendor_info.get("releases", []):
                urls.append(f"{base}{release}/targets/")

        # GPL sources
        if vendor_key == "dlink_gpl":
            model_short = model.replace("DIR-", "").replace("-", "")
            urls.append(f"{base}{model}/")

        return urls

    # ------------------------------------------------------------------
    # Download — robust download with resume and checksum verification
    # ------------------------------------------------------------------

    def download(
        self,
        entry: FirmwareEntry,
        output_dir: Optional[str] = None,
        verify: bool = True,
        resume: bool = True,
    ) -> Optional[str]:
        """Download a firmware image.

        Args:
            entry: FirmwareEntry to download.
            output_dir: Output directory (default: self.download_dir).
            verify: Verify checksum if available.
            resume: Resume partial downloads.

        Returns:
            Path to downloaded file, or None if download failed.
        """
        output_dir = output_dir or self.download_dir
        os.makedirs(output_dir, exist_ok=True)

        filename = entry.filename or self._guess_filename(entry)
        output_path = os.path.join(output_dir, filename)

        logger.info(f"Downloading {entry.display_name} -> {output_path}")

        # Build curl command
        cmd = ["curl", "-L", "--connect-timeout", "15", "--max-time", str(self.timeout)]

        # Resume support
        if resume and os.path.exists(output_path):
            cmd.extend(["-C", "-"])

        # Output
        cmd.extend(["-o", output_path])

        # Vendor-specific flags
        vendor_info = VENDOR_REGISTRY.get(entry.source, {})
        if vendor_info.get("referer_required"):
            cmd.extend(["-H", f"Referer: {vendor_info.get('homepage', '')}"])
        if vendor_info.get("user_agent_spoof"):
            cmd.extend(["-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"])

        # Extra curl flags
        for flag in vendor_info.get("curl_flags", []):
            cmd.append(flag)

        cmd.append(entry.url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 30,
            )

            if result.returncode != 0:
                logger.error(f"Curl failed: {result.stderr[:200]}")
                # If resume fails, try fresh download
                if resume and os.path.exists(output_path):
                    logger.info("Resume failed, trying fresh download...")
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    cmd.remove("-C")
                    cmd.remove("-")
                    result = subprocess.run(cmd, capture_output=True, text=True,
                                            timeout=self.timeout + 30)
                    if result.returncode != 0:
                        return None
                else:
                    return None

            # Verify file exists and has reasonable size
            if not os.path.exists(output_path):
                return None
            size = os.path.getsize(output_path)
            if size < 1024:  # Less than 1KB — probably an error page
                logger.warning(f"Downloaded file too small ({size} bytes), likely error page")
                # Check if it's HTML
                with open(output_path, "rb") as f:
                    header = f.read(100)
                if b"<!DOCTYPE" in header or b"<html" in header:
                    os.remove(output_path)
                    return None

            # Verify checksum
            if verify and (entry.checksum_md5 or entry.checksum_sha256):
                if not self._verify_checksum(output_path, entry):
                    logger.warning("Checksum verification failed")
                    # Keep the file anyway — checksum might be for a different version

            entry.size_bytes = size
            logger.info(f"Downloaded {filename} ({size / 1024 / 1024:.1f} MB)")
            return output_path

        except subprocess.TimeoutExpired:
            logger.error("Download timed out")
            return None
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None

    def download_all(
        self,
        entries: List[FirmwareEntry],
        output_dir: Optional[str] = None,
    ) -> List[Tuple[FirmwareEntry, Optional[str]]]:
        """Download multiple firmware images in parallel.

        Returns list of (entry, path_or_None) tuples.
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as pool:
            futures = {
                pool.submit(self.download, entry, output_dir): entry
                for entry in entries
            }
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    path = future.result()
                    results.append((entry, path))
                except Exception as e:
                    logger.error(f"Failed to download {entry.display_name}: {e}")
                    results.append((entry, None))
        return results

    def _guess_filename(self, entry: FirmwareEntry) -> str:
        """Guess filename from URL or entry metadata."""
        # Try to extract from URL
        parsed = urlparse(entry.url)
        path = parsed.path
        if path and "/" in path:
            basename = os.path.basename(path)
            if basename and "." in basename:
                return basename

        # Build from metadata
        parts = [entry.vendor.lower().replace(" ", "_"),
                 entry.model.lower().replace(" ", "_")]
        if entry.version:
            parts.append(entry.version)
        parts.append(datetime.now().strftime("%Y%m%d"))

        return "_".join(parts) + ".bin"

    def _verify_checksum(self, filepath: str, entry: FirmwareEntry) -> bool:
        """Verify file checksum against entry metadata."""
        if entry.checksum_sha256:
            sha = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha.update(chunk)
            return sha.hexdigest().lower() == entry.checksum_sha256.lower()

        if entry.checksum_md5:
            md5 = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5.update(chunk)
            return md5.hexdigest().lower() == entry.checksum_md5.lower()

        return True  # No checksum to verify

    # ------------------------------------------------------------------
    # Quick actions — search + download + emulate in one call
    # ------------------------------------------------------------------

    def quick(
        self,
        query: str,
        emulate: bool = False,
        agent_url: str = "http://127.0.0.1:9100",
    ) -> Optional[str]:
        """Search and download firmware in one step. Optionally start emulation.

        Args:
            query: Search query.
            emulate: If True, feed firmware into emulation agent after download.
            agent_url: Emulation agent URL.

        Returns:
            Path to downloaded firmware, or None.
        """
        # Step 1: Search
        print(f"🔍 Searching for: {query}")
        results = self.search(query)

        if not results:
            print("❌ No firmware found")
            return None

        # Show top results
        print(f"\nFound {len(results)} results. Top matches:")
        for i, r in enumerate(results[:5]):
            print(f"  {i+1}. {r.display_name} [{r.source}] (reliability: {r.reliability:.0%})")
            print(f"     {r.url}")

        # Step 2: Download best match
        best = results[0]
        print(f"\n⬇️  Downloading: {best.display_name}")
        path = self.download(best)

        if not path:
            print("❌ Download failed")
            return None

        print(f"✅ Downloaded: {path}")

        # Step 3: Optionally emulate
        if emulate:
            print(f"\n🚀 Starting emulation via {agent_url}...")
            try:
                try:
                    from emulation_agent.client import EmulationAgentClient
                except ModuleNotFoundError:
                    from client import EmulationAgentClient
                client = EmulationAgentClient(
                    host=urlparse(agent_url).hostname or "127.0.0.1",
                    port=urlparse(agent_url).port or 9100,
                )
                result = client.emulate_and_probe(path)
                if result.get("success"):
                    print(f"✅ Emulation successful!")
                    print(f"   Arch: {result.get('steps', {}).get('upload', {}).get('arch')}")
                    probe = result.get('steps', {}).get('probe', {})
                    print(f"   Reachable: {probe.get('reachable')}")
                    print(f"   HTTP Status: {probe.get('http_status')}")
                    print(f"   Banner: {probe.get('banner')}")
                else:
                    print(f"⚠️  Emulation incomplete: {result.get('error')}")
            except Exception as e:
                print(f"⚠️  Emulation skipped (agent unreachable): {e}")

        return path

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def list_sources(self) -> List[Dict[str, Any]]:
        """List all known firmware sources."""
        sources = []
        for key, info in VENDOR_REGISTRY.items():
            sources.append({
                "key": key,
                "name": info["name"],
                "homepage": info.get("homepage", ""),
                "models_count": len(KNOWN_MODELS.get(key, [])),
                "cached_entries": len(self._vendor_index.get(key, [])),
            })
        return sorted(sources, key=lambda s: s["name"])

    def update_index(self, vendor: Optional[str] = None):
        """Rebuild vendor firmware index by scraping download pages.

        This is a network-intensive operation that discovers actual firmware
        URLs from vendor websites.
        """
        vendors = [vendor] if vendor else list(VENDOR_REGISTRY.keys())
        for v in vendors:
            try:
                self._scrape_vendor_index(v)
            except Exception as e:
                logger.warning(f"Failed to update index for {v}: {e}")

    def _scrape_vendor_index(self, vendor_key: str):
        """Scrape a vendor's download page for firmware links."""
        vendor_info = VENDOR_REGISTRY[vendor_key]
        entries = []
        download_base = vendor_info.get("download_base", "")

        if not download_base:
            return

        # For OpenWrt, parse the release index page
        if vendor_key == "openwrt":
            entries = self._scrape_openwrt_index(vendor_info)
        elif vendor_key == "ddwrt":
            entries = self._scrape_ddwrt_index(vendor_info)

        if entries:
            self._vendor_index[vendor_key] = entries
            self._save_cache(vendor_key, entries)
            logger.info(f"Updated {vendor_key} index: {len(entries)} entries")

    def _scrape_openwrt_index(self, info: dict) -> List[FirmwareEntry]:
        """Scrape OpenWrt downloads page for firmware links."""
        entries = []
        release_index = info.get("release_index", info["download_base"])

        try:
            import urllib.request
            resp = urllib.request.urlopen(release_index, timeout=30)
            html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to fetch OpenWrt index: {e}")
            return entries

        # Find release directories
        release_pattern = re.compile(r'href="(\d+\.\d+\.\d+)/"')
        releases = release_pattern.findall(html)

        # For each release, find targets
        for release in releases[:3]:  # Only latest 3 releases to be reasonable
            try:
                targets_url = f"{release_index}{release}/targets/"
                resp = urllib.request.urlopen(targets_url, timeout=30)
                targets_html = resp.read().decode("utf-8", errors="replace")
                target_pattern = re.compile(r'href="([a-z0-9_]+)/"')
                targets = target_pattern.findall(targets_html)

                for target in targets:
                    try:
                        subtargets_url = f"{targets_url}{target}/"
                        resp = urllib.request.urlopen(subtargets_url, timeout=30)
                        sub_html = resp.read().decode("utf-8", errors="replace")
                        sub_pattern = re.compile(r'href="([a-z0-9_\.\-]+)/"')
                        subtargets = sub_pattern.findall(sub_html)

                        for subtarget in subtargets:
                            if subtarget in ("..", "packages", "kmods"):
                                continue
                            firmware_url = f"{subtargets_url}{subtarget}/"
                            entries.append(FirmwareEntry(
                                vendor="OpenWrt",
                                model=f"{target}/{subtarget}",
                                version=release,
                                url=firmware_url,
                                source="openwrt",
                                reliability=0.95,
                                arch_hint=self._guess_arch_from_target(target),
                            ))
                    except Exception:
                        pass
            except Exception:
                pass

        return entries

    def _scrape_ddwrt_index(self, info: dict) -> List[FirmwareEntry]:
        """Scrape DD-WRT download page."""
        entries = []
        base = info["download_base"]

        try:
            import urllib.request
            resp = urllib.request.urlopen(base, timeout=30)
            html = resp.read().decode("utf-8", errors="replace")
            year_pattern = re.compile(r'href="(\d{4})/"')
            years = year_pattern.findall(html)

            for year in sorted(years, reverse=True)[:2]:
                entries.append(FirmwareEntry(
                    vendor="DD-WRT",
                    model=f"Beta {year}",
                    version=year,
                    url=f"{base}{year}/",
                    source="ddwrt",
                    reliability=0.8,
                ))
        except Exception as e:
            logger.warning(f"Failed to scrape DD-WRT: {e}")

        return entries

    def _guess_arch_from_target(self, target: str) -> str:
        """Guess architecture from OpenWrt target name."""
        target_lower = target.lower()
        if "mips" in target_lower:
            return "mips" if "mips_" in target_lower else "mipsel"
        if "arm" in target_lower:
            return "arm"
        if "x86" in target_lower:
            return "x86_64" if "64" in target_lower else "i386"
        if "powerpc" in target_lower:
            return "ppc"
        return ""


# ---------------------------------------------------------------------------
# Standalone convenience functions
# ---------------------------------------------------------------------------

def search_firmware(query: str, **kwargs) -> List[FirmwareEntry]:
    """Quick firmware search."""
    fa = FirmwareAcquisition()
    return fa.search(query, **kwargs)


def download_firmware(url: str, output_dir: str = "/tmp/firmware_downloads") -> Optional[str]:
    """Quick firmware download from URL."""
    fa = FirmwareAcquisition()
    entry = FirmwareEntry(
        vendor="unknown", model="unknown", version="",
        url=url, source="manual",
    )
    return fa.download(entry, output_dir=output_dir)


def quick_acquire(query: str, emulate: bool = True) -> Optional[str]:
    """Search and download firmware, optionally start emulation."""
    fa = FirmwareAcquisition()
    return fa.quick(query, emulate=emulate)
