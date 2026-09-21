"""Independent golden disk capture and recovery metadata."""

import hashlib
import json
import os
import shlex
import subprocess
import xml.etree.ElementTree as ET
from functools import partial
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class GoldenMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: str
    architecture: str
    firmware: Literal["bios", "efi"]
    compressed_bytes: int = Field(gt=0)
    disk_bytes: int = Field(gt=0)
    root_partition: int = Field(gt=0)
    username: str
    provisioning: dict[str, Any]


def run(argv: list[str], *, log: Path, timeout: int = 1800) -> str:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LIBGUESTFS_BACKEND": "direct"},
        )
    except subprocess.TimeoutExpired as exc:
        with log.open("a") as stream:
            stream.write(f"{shlex.join(argv)}\n{exc}\n{exc.stdout!r}\n{exc.stderr!r}\n")
        raise
    with log.open("a") as stream:
        stream.write(shlex.join(argv) + "\n" + result.stdout + result.stderr + "\n")
    result.check_returncode()
    return result.stdout


def capture(
    source: Path,
    destination: Path,
    *,
    firmware: Literal["bios", "efi"],
    username: str,
    provisioning: dict[str, Any],
) -> Path:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = destination.parent / f".{destination.name}.capture-{uuid4().hex}"
    work.mkdir()
    execute = partial(run, log=work / "capture.log")
    qcow = work / "golden.qcow2"
    execute(
        ["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", str(source), str(qcow)]
    )
    info = json.loads(execute(["qemu-img", "info", "--output=json", str(qcow)]))
    if info.get("backing-filename"):
        raise ValueError("Captured image must not depend on a backing image")
    inspection = ET.fromstring(
        execute(["virt-inspector", "--format=qcow2", "-a", str(qcow)])
    )
    systems = inspection.findall("operatingsystem")
    if len(systems) != 1:
        raise ValueError("Capture requires exactly one installed operating system")
    system = systems[0]
    validate_system(system)
    root = system.findtext("root")
    if root is None or not root.startswith("/dev/sda") or not root[8:].isdigit():
        raise ValueError(
            f"Unsupported root layout for recovery: {root}; use a plain disk partition"
        )
    execute(
        [
            "virt-sysprep",
            "--format=qcow2",
            "-a",
            str(qcow),
            "--operations",
            "machine-id,ssh-hostkeys,ssh-userdir,net-hwaddr,udev-persistent-net,dhcp-client-state,logfiles",
            "--delete",
            "/var/lib/cloud",
            "--delete",
            "/etc/NetworkManager/system-connections/*",
            "--hostname",
            "localhost",
            "--password",
            f"{username}:disabled",
            "--root-password",
            "disabled",
        ]
    )
    raw = work / "golden.raw"
    execute(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(qcow), str(raw)])
    payload = work / "golden.raw.zst"
    execute(["zstd", "-T2", "-3", str(raw), "-o", str(payload)])
    with payload.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = GoldenMetadata(
        sha256=digest,
        source=str(source),
        architecture=system.findtext("arch") or "",
        firmware=firmware,
        compressed_bytes=payload.stat().st_size,
        disk_bytes=info["virtual-size"],
        root_partition=int(root[8:]),
        username=username,
        provisioning=provisioning,
    )
    path = work / "golden.json"
    path.write_text(metadata.model_dump_json(indent=2))
    payload.chmod(0o644)
    qcow.chmod(0o644)
    raw.unlink()
    work.rename(destination)
    return destination / path.name


def validate_system(system: ET.Element) -> None:
    """Reject layouts and guest environments the recovery program cannot configure."""
    if system.findtext("name") != "linux":
        raise ValueError("Golden recovery requires a Linux guest")
    packages = {
        entry.findtext("name") for entry in system.findall("applications/application")
    }
    required = {
        "NetworkManager",
        "systemd",
        "openssh-server",
        "policycoreutils",
        "sudo",
    }
    if missing := required - packages:
        raise ValueError(
            f"Golden recovery requires guest packages: {', '.join(sorted(missing))}"
        )
    for mount in system.findall("mountpoints/mountpoint"):
        device = mount.get("dev", "")
        if mount.text not in {"/", "/boot", "/boot/efi"} or not device.startswith(
            "/dev/sda"
        ):
            raise ValueError(f"Unsupported recovery mount: {device} on {mount.text}")
