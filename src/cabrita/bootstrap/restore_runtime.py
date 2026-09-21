"""Standalone recovery program embedded in the installer Kickstart."""

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen


class Input(ctypes.Structure):
    _fields_ = [
        ("src", ctypes.c_void_p),
        ("size", ctypes.c_size_t),
        ("pos", ctypes.c_size_t),
    ]


class Output(ctypes.Structure):
    _fields_ = [
        ("dst", ctypes.c_void_p),
        ("size", ctypes.c_size_t),
        ("pos", ctypes.c_size_t),
    ]


def decompress(source: Path, target: Path, maximum: int) -> int:
    lib = ctypes.CDLL("libzstd.so.1")
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Output),
        ctypes.POINTER(Input),
    ]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p
    context = lib.ZSTD_createDStream()
    if not context:
        raise MemoryError("Cannot allocate zstd decoder")
    total = 0
    remaining = 1
    try:
        lib.ZSTD_initDStream(context)
        with source.open("rb") as src, target.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                buffer = ctypes.create_string_buffer(chunk)
                incoming = Input(ctypes.cast(buffer, ctypes.c_void_p), len(chunk), 0)
                while incoming.pos < incoming.size:
                    storage = ctypes.create_string_buffer(1024 * 1024)
                    outgoing = Output(
                        ctypes.cast(storage, ctypes.c_void_p), len(storage), 0
                    )
                    remaining = lib.ZSTD_decompressStream(
                        context, ctypes.byref(outgoing), ctypes.byref(incoming)
                    )
                    if lib.ZSTD_isError(remaining):
                        raise ValueError(lib.ZSTD_getErrorName(remaining).decode())
                    total += outgoing.pos
                    if total > maximum:
                        raise ValueError("Payload exceeds declared disk size")
                    dst.write(storage.raw[: outgoing.pos])
            if remaining:
                raise ValueError("Truncated zstd frame")
    finally:
        lib.ZSTD_freeDStream(context)
    return total


def write_verified_payload(payload: Path, disk: Path, disk_bytes: int) -> None:
    """Validate the entire compressed frame and size before opening the target."""
    if decompress(payload, Path("/dev/null"), disk_bytes) != disk_bytes:
        raise ValueError("Payload disk size differs from metadata")
    if decompress(payload, disk, disk_bytes) != disk_bytes:
        raise ValueError("Restored disk size differs from metadata")


def command(argv: list[str], timeout: int = 120) -> str:
    return subprocess.run(
        argv, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout


def download(url: str, destination: Path, sha256: str, expected_bytes: int) -> None:
    deadline = time.monotonic() + 1800
    digest = hashlib.sha256()
    received = 0
    with urlopen(url, timeout=30) as source, destination.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            if time.monotonic() > deadline:
                raise TimeoutError("Recovery payload download timed out")
            received += len(chunk)
            if received > expected_bytes:
                raise ValueError("Recovery payload exceeds recorded size")
            digest.update(chunk)
            target.write(chunk)
    if received != expected_bytes or digest.hexdigest() != sha256:
        raise ValueError("Recovery payload integrity check failed")


def apply_identity(root: Path, identity: dict[str, Any]) -> None:
    username = identity["username"]
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise ValueError("Invalid recovery username")
    accounts = [
        line.split(":") for line in (root / "etc/passwd").read_text().splitlines()
    ]
    if username not in {entry[0] for entry in accounts}:
        command(["chroot", str(root), "useradd", "-m", "-G", "wheel", username])
        accounts = [
            line.split(":") for line in (root / "etc/passwd").read_text().splitlines()
        ]
    account = next(entry for entry in accounts if entry[0] == username)
    command(["chroot", str(root), "usermod", "-p", "!", username])
    ssh = root / account[5].lstrip("/") / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True, mode=0o700)
    authorized = ssh / "authorized_keys"
    authorized.write_text(identity["public_key"].strip() + "\n")
    authorized.chmod(0o600)
    for path in (ssh, authorized):
        os.chown(path, int(account[2]), int(account[3]))
    (root / "etc/hostname").write_text(identity["hostname"] + "\n")
    (root / "etc/hosts").write_text(
        "127.0.0.1 localhost localhost.localdomain\n"
        "::1 localhost localhost.localdomain\n"
        f"{identity['ip']} {identity['hostname']}\n"
    )
    (root / "etc/machine-id").write_text("")
    for key in (root / "etc/ssh").glob("ssh_host_*"):
        key.unlink()
    cloud = root / "etc/cloud"
    cloud.mkdir(exist_ok=True)
    (cloud / "cloud-init.disabled").touch()
    if (root / "var/lib/cloud").exists():
        shutil.rmtree(root / "var/lib/cloud")
    connections = root / "etc/NetworkManager/system-connections"
    connections.mkdir(parents=True, exist_ok=True)
    for path in connections.iterdir():
        if path.is_file():
            path.unlink()
    profile = connections / "cabrita.nmconnection"
    profile.write_text(
        "[connection]\nid=cabrita\ntype=ethernet\nautoconnect=true\n"
        f"[ethernet]\nmac-address={identity['mac']}\n"
        f"[ipv4]\nmethod=manual\naddress1={identity['ip']}/{identity['prefix']},{identity['gateway']}\n"
        f"dns={identity['dns']};\n[ipv6]\nmethod=disabled\n"
    )
    profile.chmod(0o600)
    sudoers = root / f"etc/sudoers.d/{username}"
    sudoers.write_text(f"{username} ALL=(ALL) NOPASSWD: ALL\n")
    sudoers.chmod(0o440)
    command(["chroot", str(root), "restorecon", "-RF", "/etc", account[5]])
    (root / "var/lib/cabrita").mkdir(parents=True, exist_ok=True)
    (root / "var/lib/cabrita/restored.json").write_text(json.dumps(identity))


def restore(config: dict[str, Any]) -> None:
    metadata, identity = config["metadata"], config["identity"]
    disk = Path(config["target_disk"])
    if not disk.is_block_device():
        raise ValueError("Restoration target must be a declared block device")
    if command(["lsblk", "-dn", "-o", "TYPE", str(disk)]).strip() != "disk":
        raise ValueError("Restoration requires a whole disk, not a partition")
    if platform.machine() != metadata["architecture"]:
        raise ValueError("Golden image architecture mismatch")
    firmware = "efi" if Path("/sys/firmware/efi").exists() else "bios"
    if firmware != metadata["firmware"]:
        raise ValueError("Golden image firmware mismatch")
    if int(command(["blockdev", "--getsize64", str(disk)])) < metadata["disk_bytes"]:
        raise ValueError("Target disk is smaller than the golden image")
    mounted = json.loads(command(["lsblk", "-J", "-o", "NAME,MOUNTPOINTS", str(disk)]))

    def check_mounts(devices):
        for device in devices:
            if any(device.get("mountpoints") or []):
                raise ValueError("Refusing to overwrite a mounted disk")
            check_mounts(device.get("children", []))

    check_mounts(mounted["blockdevices"])
    payload = Path("/run/cabrita-payload.zst")
    if shutil.disk_usage(payload.parent).free < metadata["compressed_bytes"]:
        raise ValueError(
            "Installer has insufficient space to verify the compressed payload"
        )
    interfaces = [
        path.parent.name
        for path in Path("/sys/class/net").glob("*/address")
        if path.read_text().strip().lower() == identity["mac"].lower()
    ]
    if len(interfaces) != 1:
        raise ValueError("Cannot identify recovery network interface by MAC")
    command(
        [
            "nmcli",
            "connection",
            "add",
            "type",
            "ethernet",
            "ifname",
            interfaces[0],
            "con-name",
            "cabrita-recovery",
            "ipv4.method",
            "manual",
            "ipv4.addresses",
            f"{identity['ip']}/{identity['prefix']}",
            "ipv4.gateway",
            identity["gateway"],
            "ipv4.dns",
            identity["dns"],
        ]
    )
    command(["nmcli", "connection", "up", "cabrita-recovery"])
    download(
        config["payload_url"], payload, metadata["sha256"], metadata["compressed_bytes"]
    )
    # All compatibility and integrity checks precede the first target-disk write.
    write_verified_payload(payload, disk, metadata["disk_bytes"])
    command(["blockdev", "--flushbufs", str(disk)])
    command(["partprobe", str(disk)])
    command(["udevadm", "settle", "--timeout=30"])
    partition = (
        str(disk)
        + ("p" if str(disk)[-1].isdigit() else "")
        + str(metadata["root_partition"])
    )
    root = Path("/mnt/cabrita-recovery")
    root.mkdir(parents=True, exist_ok=True)
    command(["mount", partition, str(root)])
    try:
        apply_identity(root, identity)
    finally:
        command(["umount", str(root)])
    command(["sync"])
    command(["systemctl", "--no-block", "poweroff"])
    # Never return to Anaconda and accidentally start an OS installation.
    while True:
        time.sleep(1)
