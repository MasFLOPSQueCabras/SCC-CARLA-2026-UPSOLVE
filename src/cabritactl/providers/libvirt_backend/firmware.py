"""Select split flash firmware, avoiding stateless/confidential-computing ROMs."""

import fnmatch
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def select_firmware(
    capabilities: str,
    directories: tuple[Path, ...] = (
        Path("/usr/share/qemu/firmware"),
        Path("/etc/qemu/firmware"),
    ),
) -> dict[str, str]:
    root = ET.fromstring(capabilities)
    machine = root.findtext("machine", "")
    arch = root.findtext("arch", "")
    descriptors: dict[str, Path] = {}
    for directory in directories:
        descriptors.update({p.name: p for p in directory.glob("*.json")})
    for name, path in sorted(descriptors.items()):
        if path.stat().st_size == 0:
            continue
        document: dict[str, Any] = json.loads(path.read_text())
        mapping = document.get("mapping", {})
        features = document.get("features", [])
        if mapping.get("device") != "flash" or "uefi" not in document.get(
            "interface-types", []
        ):
            continue
        if "secure-boot" in features or "enrolled-keys" in features:
            continue
        if not any(
            target.get("architecture") == arch
            and any(
                fnmatch.fnmatchcase(machine, pattern)
                for pattern in target.get("machines", [])
            )
            for target in document.get("targets", [])
        ):
            continue
        code, nvram = mapping.get("executable", {}), mapping.get("nvram-template", {})
        if not code.get("filename") or not nvram.get("filename"):
            continue
        if not all(Path(item["filename"]).is_file() for item in (code, nvram)):
            continue
        return {
            "firmware_loader": code["filename"],
            "firmware_loader_format": code.get("format", "raw"),
            "firmware_nvram": nvram["filename"],
            "firmware_nvram_format": nvram.get("format", "raw"),
        }
    raise ValueError(
        f"No ordinary split CODE/VARS UEFI firmware for {arch}/{machine}; install edk2-ovmf (Fedora) or ovmf (Ubuntu)"
    )
