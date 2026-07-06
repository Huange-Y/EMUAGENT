"""
Firmware Manager — extraction, architecture detection, binary cataloging.

Handles firmware image parsing, rootfs extraction (binwalk, unsquashfs, etc.),
ELF architecture detection, and binary dependency analysis.
"""

import os
import re
import sys
import json
import gzip
import shutil
import hashlib
import logging

# Force English locale for consistent subprocess output parsing
_ENV_ENGLISH = {**os.environ, "LANG": "C", "LC_ALL": "C"}
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BinaryInfo:
    """Information about an ELF binary in the firmware."""
    path: str           # Relative path within rootfs
    arch: str           # Detected architecture
    bits: int           # 32 or 64
    endian: str         # "little" or "big"
    linked: str         # "static" or "dynamic"
    interpreter: Optional[str] = None  # Dynamic linker path
    needed_libs: List[str] = field(default_factory=list)
    stripped: bool = True
    has_symbols: bool = False


@dataclass
class RootfsInfo:
    """Metadata about an extracted rootfs."""
    rootfs_id: str
    rootfs_path: str
    arch: str = "unknown"
    arch_confidence: float = 0.0
    binary_count: int = 0
    binaries: List[BinaryInfo] = field(default_factory=list)
    created_at: str = ""
    source_file: str = ""
    source_hash: str = ""


class FirmwareManager:
    """Manages firmware extraction, architecture detection, and binary cataloging."""

    def __init__(self, rootfs_base_dir: str = "/tmp/emulation_agent/rootfs"):
        self.rootfs_base_dir = os.path.abspath(rootfs_base_dir)
        os.makedirs(self.rootfs_base_dir, exist_ok=True)
        self.rootfs_registry: Dict[str, RootfsInfo] = {}
        self._load_registry()

    def _load_registry(self):
        """Load existing rootfs registry from disk."""
        registry_path = os.path.join(self.rootfs_base_dir, "registry.json")
        if os.path.exists(registry_path):
            try:
                with open(registry_path) as f:
                    data = json.load(f)
                for rid, info_dict in data.items():
                    info = RootfsInfo(**info_dict)
                    if os.path.isdir(info.rootfs_path):
                        self.rootfs_registry[rid] = info
                logger.info(f"Loaded {len(self.rootfs_registry)} rootfs from registry")
            except Exception as e:
                logger.warning(f"Failed to load registry: {e}")

    def _save_registry(self):
        """Save rootfs registry to disk."""
        registry_path = os.path.join(self.rootfs_base_dir, "registry.json")
        data = {}
        for rid, info in self.rootfs_registry.items():
            data[rid] = {
                "rootfs_id": info.rootfs_id,
                "rootfs_path": info.rootfs_path,
                "arch": info.arch,
                "arch_confidence": info.arch_confidence,
                "binary_count": info.binary_count,
                "created_at": info.created_at,
                "source_file": info.source_file,
                "source_hash": info.source_hash,
            }
        with open(registry_path, "w") as f:
            json.dump(data, f, indent=2)

    # ------------------------------------------------------------------
    # Firmware extraction
    # ------------------------------------------------------------------

    def extract_rootfs(
        self,
        file_path: Optional[str] = None,
        file_data: Optional[bytes] = None,
        format_hint: str = "auto",
    ) -> RootfsInfo:
        """Extract root filesystem from a firmware image.

        Args:
            file_path: Path to firmware image or rootfs archive on disk.
            file_data: Raw bytes of firmware (alternative to file_path).
            format_hint: "auto", "tar.gz", "zip", "squashfs", "bin", "cpio".

        Returns:
            RootfsInfo with rootfs_id, path, and initial metadata.
        """
        # Determine source and compute hash
        if file_data:
            source_hash = hashlib.sha256(file_data).hexdigest()[:16]
            source_name = "uploaded_data"
        elif file_path:
            with open(file_path, "rb") as f:
                file_data = f.read()
            source_hash = hashlib.sha256(file_data).hexdigest()[:16]
            source_name = os.path.basename(file_path)
        else:
            raise ValueError("Either file_path or file_data must be provided")

        # Check if already extracted
        for existing in self.rootfs_registry.values():
            if existing.source_hash == source_hash:
                logger.info(f"Rootfs already extracted: {existing.rootfs_id}")
                return existing

        rootfs_id = f"fw_{source_hash}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        extract_dir = os.path.join(self.rootfs_base_dir, rootfs_id)
        os.makedirs(extract_dir, exist_ok=True)

        # Write to temp file for extraction tools that need a file
        tmp_path = os.path.join(extract_dir, "_firmware_input")
        with open(tmp_path, "wb") as f:
            f.write(file_data)

        # Determine actual format
        if format_hint == "auto":
            format_hint = self._detect_format(file_data, source_name)

        logger.info(f"Extracting {source_name} as {format_hint} -> {extract_dir}")

        try:
            self._extract_by_format(tmp_path, extract_dir, format_hint)
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            # Try binwalk as fallback for raw firmware
            if format_hint != "binwalk":
                logger.info("Falling back to binwalk extraction...")
                try:
                    self._extract_binwalk(tmp_path, extract_dir)
                except Exception as e2:
                    logger.error(f"Binwalk fallback also failed: {e2}")
                    raise RuntimeError(f"All extraction methods failed: {e}, {e2}") from e2

        # Clean up temp input file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        # Find the actual rootfs directory (binwalk creates subdirectories)
        actual_rootfs = self._find_rootfs_dir(extract_dir)

        # Detect architecture and catalog binaries
        arch, confidence = self.detect_architecture(actual_rootfs)
        binaries = self.list_binaries(actual_rootfs)

        info = RootfsInfo(
            rootfs_id=rootfs_id,
            rootfs_path=actual_rootfs,
            arch=arch,
            arch_confidence=confidence,
            binary_count=len(binaries),
            binaries=binaries,
            created_at=datetime.now().isoformat(),
            source_file=source_name,
            source_hash=source_hash,
        )

        self.rootfs_registry[rootfs_id] = info
        self._save_registry()
        logger.info(
            f"Extracted rootfs {rootfs_id}: arch={arch} (confidence={confidence:.0%}), "
            f"{len(binaries)} binaries"
        )
        return info

    def _detect_format(self, data: bytes, filename: str) -> str:
        """Auto-detect firmware/archive format from magic bytes and filename."""
        # Check magic bytes
        if data[:2] == b'\x1f\x8b':
            return "tar.gz"
        if data[:4] == b'PK\x03\x04':
            return "zip"
        if data[:4] == b'hsqs' or data[:4] == b'sqsh':
            return "squashfs"
        if data[:6] == b'070707' or data[:6] == b'070701':
            return "cpio"
        if data[:4] == b'\x7fELF':
            return "single_elf"
        if data[:3] == b'UBI' or data[:4] == b'UBI#':
            return "ubi"
        if data[:4] == b'JFFS':
            return "jffs2"

        # Fallback to filename-based detection
        name_lower = filename.lower()
        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            return "tar.gz"
        if name_lower.endswith(".tar"):
            return "tar"
        if name_lower.endswith(".zip"):
            return "zip"
        if name_lower.endswith(".bin") or name_lower.endswith(".img"):
            return "binwalk"
        if name_lower.endswith(".chk"):
            return "binwalk"  # Common in D-Link/Netgear

        # Default: try binwalk
        return "binwalk"

    def _extract_by_format(self, input_path: str, output_dir: str, fmt: str):
        """Extract firmware using the appropriate tool for the format."""
        if fmt == "tar.gz":
            subprocess.run(
                ["tar", "-xzf", input_path, "-C", output_dir],
                check=True, capture_output=True, timeout=120,
            )
        elif fmt == "tar":
            subprocess.run(
                ["tar", "-xf", input_path, "-C", output_dir],
                check=True, capture_output=True, timeout=120,
            )
        elif fmt == "zip":
            subprocess.run(
                ["unzip", "-o", input_path, "-d", output_dir],
                check=True, capture_output=True, timeout=120,
            )
        elif fmt == "squashfs":
            subprocess.run(
                ["unsquashfs", "-f", "-d", output_dir, input_path],
                check=True, capture_output=True, timeout=120,
            )
        elif fmt == "cpio":
            with open(input_path, "rb") as f:
                subprocess.run(
                    ["cpio", "-idmv"],
                    cwd=output_dir, stdin=f,
                    check=True, capture_output=True, timeout=120,
                )
        elif fmt in ("binwalk", "bin", "ubi", "jffs2"):
            self._extract_binwalk(input_path, output_dir)
        elif fmt == "single_elf":
            # Single ELF file — just copy
            os.makedirs(os.path.join(output_dir, "bin"), exist_ok=True)
            shutil.copy(input_path, os.path.join(output_dir, "bin", "target.elf"))
        else:
            raise ValueError(f"Unknown format: {fmt}")

    def _extract_binwalk(self, input_path: str, output_dir: str):
        """Extract firmware using binwalk."""
        cmd = [
            "binwalk", "-Me", "-d", "2",  # depth limit 2
            "--directory", output_dir,
            input_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        logger.debug(f"Binwalk output: {result.stdout[:500]}")
        if result.returncode != 0:
            logger.warning(f"Binwalk returned {result.returncode}: {result.stderr[:200]}")

    def _find_rootfs_dir(self, base_dir: str) -> str:
        """Find the actual rootfs directory within extracted output.

        Looks for typical rootfs indicators: /bin, /sbin, /etc, /lib.
        """
        # Check if base_dir itself looks like a rootfs
        if self._looks_like_rootfs(base_dir):
            return base_dir

        # Search subdirectories for squashfs-root or similar
        for root, dirs, files in os.walk(base_dir):
            # Skip deep nesting
            depth = root[len(base_dir):].count(os.sep)
            if depth > 5:
                continue
            if self._looks_like_rootfs(root):
                return root

        # If nothing found, return base_dir as-is
        logger.warning(f"No clear rootfs directory found in {base_dir}, using base dir")
        return base_dir

    def _looks_like_rootfs(self, path: str) -> bool:
        """Check if a directory looks like a Linux root filesystem."""
        indicators = ["bin", "sbin", "etc", "lib", "usr"]
        found = sum(1 for d in indicators if os.path.isdir(os.path.join(path, d)))
        return found >= 2

    # ------------------------------------------------------------------
    # Architecture detection
    # ------------------------------------------------------------------

    def detect_architecture(self, rootfs_path: str) -> Tuple[str, float]:
        """Detect the primary CPU architecture of binaries in a rootfs.

        Returns:
            (arch_string, confidence) — e.g. ("mipsel", 0.95)
        """
        binaries = self._find_elf_binaries(rootfs_path)
        if not binaries:
            logger.warning(f"No ELF binaries found in {rootfs_path}")
            return ("unknown", 0.0)

        arch_votes: Dict[str, int] = {}
        arch_samples: Dict[str, List[str]] = {}

        for bin_path in binaries[:50]:  # Sample up to 50 binaries
            arch = self._detect_elf_arch(bin_path)
            if arch:
                arch_votes[arch] = arch_votes.get(arch, 0) + 1
                arch_samples.setdefault(arch, []).append(
                    os.path.relpath(bin_path, rootfs_path)
                )

        if not arch_votes:
            return ("unknown", 0.0)

        # Find majority architecture
        total = sum(arch_votes.values())
        best_arch = max(arch_votes, key=arch_votes.get)
        confidence = arch_votes[best_arch] / total

        logger.info(
            f"Arch detection: {best_arch} (confidence={confidence:.0%}), "
            f"votes={dict(arch_votes)}, samples={arch_samples.get(best_arch, [])[:3]}"
        )
        return (best_arch, confidence)

    def _find_elf_binaries(self, rootfs_path: str) -> List[str]:
        """Find all ELF binaries in the rootfs."""
        elfs = []
        search_dirs = ["bin", "sbin", "usr/bin", "usr/sbin", "lib", "usr/lib"]
        for dirname in search_dirs:
            search_path = os.path.join(rootfs_path, dirname)
            if not os.path.isdir(search_path):
                continue
            for fname in os.listdir(search_path):
                fpath = os.path.join(search_path, fname)
                if os.path.islink(fpath):
                    continue
                if os.path.isfile(fpath) and os.access(fpath, os.R_OK):
                    try:
                        with open(fpath, "rb") as f:
                            magic = f.read(4)
                        if magic == b'\x7fELF':
                            elfs.append(fpath)
                    except (IOError, OSError):
                        pass
        return elfs

    def _detect_elf_arch(self, elf_path: str) -> Optional[str]:
        """Detect architecture of a single ELF binary using readelf."""
        try:
            result = subprocess.run(
                ["readelf", "-h", elf_path],
                capture_output=True, text=True, timeout=5, env=_ENV_ENGLISH,
            )
            if result.returncode != 0:
                return None

            output = result.stdout
            # Parse machine type
            machine_match = re.search(r'Machine:\s*(.+)', output)
            class_match = re.search(r"Class:\s*(.+)", output)
            data_match = re.search(r"Data:\s*(.+)", output)

            if not machine_match:
                return None

            machine = machine_match.group(1).strip()
            elf_class = class_match.group(1).strip() if class_match else ""
            endian = data_match.group(1).strip() if data_match else ""

            is_64 = "64" in elf_class
            is_be = "big" in endian.lower()

            # Map readelf output to our arch names
            machine_lower = machine.lower()
            if "mips" in machine_lower:
                if is_64:
                    return "mips64" if is_be else "mips64el"
                return "mips" if is_be else "mipsel"
            elif "arm" in machine_lower or "aarch" in machine_lower:
                if "aarch64" in machine_lower:
                    return "aarch64_be" if is_be else "aarch64"
                return "armeb" if is_be else "arm"
            elif "intel" in machine_lower or "x86" in machine_lower or "i386" in machine_lower or "i486" in machine_lower:
                return "x86_64" if is_64 else "i386"
            elif "powerpc" in machine_lower:
                return "ppc"
            elif "sparc" in machine_lower:
                return "sparc"
            else:
                # Unknown — try to normalize
                logger.debug(f"Unknown machine type from readelf: {machine}")
                return machine_lower.replace(" ", "_")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"readelf failed for {elf_path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Binary cataloging
    # ------------------------------------------------------------------

    def list_binaries(self, rootfs_path: str, max_count: int = 200) -> List[BinaryInfo]:
        """List and analyze all ELF binaries in the rootfs.

        Returns detailed BinaryInfo for each, including library dependencies.
        """
        elf_paths = self._find_elf_binaries(rootfs_path)
        results = []

        for elf_path in elf_paths[:max_count]:
            try:
                info = self._analyze_elf(elf_path, rootfs_path)
                if info:
                    results.append(info)
            except Exception as e:
                logger.debug(f"Failed to analyze {elf_path}: {e}")

        # Sort: executables first, then libraries
        results.sort(key=lambda b: (
            0 if any(d in b.path for d in ["bin/", "sbin/"]) else 1,
            b.path,
        ))
        return results

    def _analyze_elf(self, elf_path: str, rootfs_path: str) -> Optional[BinaryInfo]:
        """Analyze a single ELF binary for arch, linkage, deps."""
        try:
            readelf_result = subprocess.run(
                ["readelf", "-h", "-d", "-l", elf_path],
                capture_output=True, text=True, timeout=5, env=_ENV_ENGLISH,
            )
            if readelf_result.returncode != 0:
                return None

            output = readelf_result.stdout

            # Architecture
            machine_match = re.search(r'Machine:\s*(.+)', output)
            class_match = re.search(r"Class:\s*(.+)", output)
            data_match = re.search(r"Data:\s*(.+)", output)

            arch = self._detect_elf_arch(elf_path) or "unknown"
            bits = 64 if class_match and "64" in class_match.group(1) else 32
            endian = "little"
            if data_match:
                endian = "little" if "little" in data_match.group(1).lower() else "big"

            # Linkage type
            is_static = "statically linked" in output.lower() or "STATIC" in output
            linked = "static" if is_static else "dynamic"

            # Dynamic linker / interpreter
            interpreter = None
            interp_match = re.search(r'\[Requesting program interpreter:\s*(.+?)\]', output)
            if interp_match:
                interpreter = interp_match.group(1).strip()

            # Needed libraries
            needed_libs = []
            for line in output.split("\n"):
                if "NEEDED" in line:
                    lib_match = re.search(r'Shared library:\s*\[(.+?)\]', line)
                    if lib_match:
                        needed_libs.append(lib_match.group(1))

            # Symbol info
            stripped = True
            has_symbols = False
            try:
                sym_result = subprocess.run(
                    ["readelf", "-s", elf_path],
                    capture_output=True, text=True, timeout=5, env=_ENV_ENGLISH,
                )
                if sym_result.returncode == 0 and "Symbol table" in sym_result.stdout:
                    has_symbols = ".symtab" in sym_result.stdout
                    stripped = not has_symbols
            except Exception:
                pass

            rel_path = os.path.relpath(elf_path, rootfs_path)

            return BinaryInfo(
                path=rel_path,
                arch=arch,
                bits=bits,
                endian=endian,
                linked=linked,
                interpreter=interpreter,
                needed_libs=needed_libs,
                stripped=stripped,
                has_symbols=has_symbols,
            )
        except Exception as e:
            logger.debug(f"ELF analysis failed for {elf_path}: {e}")
            return None

    def find_library_deps(self, binary_path: str, rootfs_path: str) -> List[str]:
        """Find all required shared libraries for a binary, recursively."""
        abs_path = os.path.join(rootfs_path, binary_path)
        analyzed = set()
        missing = set()
        found = []

        def _resolve_lib(lib_name: str) -> Optional[str]:
            """Resolve a library name to a path within the rootfs."""
            search_paths = [
                "", "lib/", "usr/lib/", "lib/mipsel-linux-gnu/",
                "lib/mips-linux-gnu/", "lib/arm-linux-gnueabi/",
                "lib/aarch64-linux-gnu/",
            ]
            for sp in search_paths:
                candidate = os.path.join(rootfs_path, sp, lib_name)
                if os.path.isfile(candidate):
                    return os.path.relpath(candidate, rootfs_path)
            return None

        info = self._analyze_elf(abs_path, rootfs_path)
        if not info:
            return []

        queue = list(info.needed_libs)
        while queue:
            lib = queue.pop(0)
            if lib in analyzed:
                continue
            analyzed.add(lib)
            resolved = _resolve_lib(lib)
            if resolved:
                found.append(resolved)
                # Recurse into this lib's dependencies
                abs_resolved = os.path.join(rootfs_path, resolved)
                lib_info = self._analyze_elf(abs_resolved, rootfs_path)
                if lib_info:
                    for dep in lib_info.needed_libs:
                        if dep not in analyzed:
                            queue.append(dep)
            else:
                missing.add(lib)

        if missing:
            logger.debug(f"Missing libraries for {binary_path}: {missing}")

        return found

    # ------------------------------------------------------------------
    # File-based operations
    # ------------------------------------------------------------------

    def upload_rootfs_file(self, file_path: str, format_hint: str = "auto") -> RootfsInfo:
        """Upload and extract a rootfs file. Convenience wrapper around extract_rootfs."""
        return self.extract_rootfs(file_path=file_path, format_hint=format_hint)

    def upload_rootfs_data(self, data: bytes, format_hint: str = "auto") -> RootfsInfo:
        """Upload and extract rootfs from raw bytes."""
        return self.extract_rootfs(file_data=data, format_hint=format_hint)

    def get_rootfs(self, rootfs_id: str) -> Optional[RootfsInfo]:
        """Get rootfs info by ID."""
        return self.rootfs_registry.get(rootfs_id)

    def list_rootfs(self) -> List[RootfsInfo]:
        """List all extracted rootfs."""
        return list(self.rootfs_registry.values())

    def delete_rootfs(self, rootfs_id: str) -> bool:
        """Delete an extracted rootfs and its registry entry."""
        info = self.rootfs_registry.get(rootfs_id)
        if not info:
            return False
        if os.path.isdir(info.rootfs_path):
            shutil.rmtree(info.rootfs_path, ignore_errors=True)
        del self.rootfs_registry[rootfs_id]
        self._save_registry()
        logger.info(f"Deleted rootfs {rootfs_id}")
        return True

    def get_binary_info(self, rootfs_id: str, binary_path: str) -> Optional[BinaryInfo]:
        """Get detailed info about a specific binary in a rootfs."""
        info = self.rootfs_registry.get(rootfs_id)
        if not info:
            return None
        abs_path = os.path.join(info.rootfs_path, binary_path)
        if not os.path.isfile(abs_path):
            return None
        return self._analyze_elf(abs_path, info.rootfs_path)

    def find_binary(self, rootfs_id: str, binary_name: str) -> Optional[str]:
        """Find a binary by name within a rootfs. Returns relative path or None."""
        info = self.rootfs_registry.get(rootfs_id)
        if not info:
            return None
        for b in info.binaries:
            if os.path.basename(b.path) == binary_name:
                return b.path
        # Also search filesystem directly
        search_dirs = ["bin", "sbin", "usr/bin", "usr/sbin"]
        for d in search_dirs:
            candidate = os.path.join(info.rootfs_path, d, binary_name)
            if os.path.isfile(candidate):
                return os.path.relpath(candidate, info.rootfs_path)
        return None

    def cleanup_all(self):
        """Remove all extracted rootfs."""
        for rid in list(self.rootfs_registry.keys()):
            self.delete_rootfs(rid)
