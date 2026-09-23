"""QEMU-visible data lives in a libvirt pool, never in a user's private cache."""

import hashlib
import json
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


@contextmanager
def volume_lock(pool: str, name: str):
    key = hashlib.sha256(f"{pool}/{name}".encode()).hexdigest()
    with socket.socket(socket.AF_UNIX) as lock:
        deadline = time.monotonic() + 1800
        while True:
            try:
                lock.bind("\0cabritactl-volume-" + key)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Storage volume is busy: {name}") from None
                time.sleep(0.1)
        yield


def volume_xml(
    name: str,
    capacity: int,
    fmt: str,
    *,
    backing: str | None = None,
    description: str = "",
) -> str:
    root = ET.Element("volume")
    ET.SubElement(root, "name").text = name
    ET.SubElement(root, "capacity", unit="bytes").text = str(capacity)
    ET.SubElement(root, "allocation").text = "0"
    target = ET.SubElement(root, "target")
    ET.SubElement(target, "format", type=fmt)
    ET.SubElement(ET.SubElement(target, "permissions"), "mode").text = "0600"
    if backing:
        store = ET.SubElement(root, "backingStore")
        ET.SubElement(store, "path").text = backing
        ET.SubElement(store, "format", type="qcow2")
    return ET.tostring(root, encoding="unicode")


class ManagedStorage:
    def __init__(self, conn: Any, pool_name: str, cluster: str):
        self.conn, self.pool_name, self.cluster = conn, pool_name, cluster

    @property
    def receipt(self) -> Path:
        from cabritactl.paths import get_state_dir

        return get_state_dir() / "clusters" / self.cluster / "storage.json"

    def records(self) -> dict:
        return json.loads(self.receipt.read_text()) if self.receipt.exists() else {}

    def remember(self, volume: Any, digest: str = "") -> None:
        with volume_lock(self.pool_name, "receipt-" + self.cluster):
            records = self.records()
            records[volume.name()] = {
                "pool": self.pool_name,
                "key": volume.key(),
                "path": volume.path(),
                "sha256": digest,
            }
            self.receipt.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.receipt.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, indent=2))
            temporary.replace(self.receipt)

    def owns(self, volume: Any) -> bool:
        record = self.records().get(volume.name(), {})
        return (
            record.get("pool") == self.pool_name
            and record.get("key") == volume.key()
            and record.get("path") == volume.path()
        )

    @property
    def pool(self):
        pools = {p.name(): p for p in self.conn.listAllStoragePools()}
        if self.pool_name not in pools:
            raise RuntimeError(
                f"Libvirt pool {self.pool_name} is missing; run cabritactl host setup --apply"
            )
        pool = pools[self.pool_name]
        if not pool.isActive():
            raise RuntimeError(
                f"Libvirt pool {self.pool_name} is inactive; start it before deployment"
            )
        root = ET.fromstring(pool.XMLDesc(0))
        if root.get("type") != "dir":
            raise ValueError("Cabrita currently requires a directory storage pool")
        return pool

    def lookup(self, name: str):
        pool = self.pool
        # Do not turn permission/connection errors into "missing".
        return next((v for v in pool.listAllVolumes() if v.name() == name), None)

    def download(self, volume: Any, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stream = self.conn.newStream(0)
        try:
            volume.download(stream, 0, 0, 0)
            with destination.open("wb") as output:
                stream.recvAll(lambda _s, data, _o: output.write(data), None)
            stream.finish()
        except BaseException:
            with suppress(Exception):
                stream.abort()
            raise
        destination.chmod(0o600)

    def _digest(self, volume: Any) -> str:
        stream = self.conn.newStream(0)
        digest = hashlib.sha256()

        def receive(_stream, data, _opaque):
            digest.update(data)
            return len(data)

        try:
            volume.download(stream, 0, 0, 0)
            stream.recvAll(receive, None)
            stream.finish()
        except BaseException:
            with suppress(Exception):
                stream.abort()
            raise
        return digest.hexdigest()

    def import_file(self, source: Path, name: str | None = None) -> Path:
        with source.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        name = name or f"base-{digest}{source.suffix}"
        description = f"cabritactl:sha256:{digest}"
        with volume_lock(self.pool_name, name):
            volume = self.lookup(name)
            if volume is not None:
                if not name.startswith("base-") and not self.owns(volume):
                    raise ValueError(f"Refusing to adopt unowned volume {name}")
                if self._digest(volume) == digest:
                    return Path(volume.path())
                if not self.owns(volume):
                    raise ValueError(
                        f"Unowned base volume {name} has an invalid checksum"
                    )
                # A killed upload may leave a partial volume. Only replace it if no VM references it.
                for domain in self.conn.listAllDomains():
                    if str(volume.path()) in domain.XMLDesc(0):
                        raise ValueError(
                            f"Corrupt managed volume {name} is still referenced by {domain.name()}"
                        )
                volume.delete(0)
            # Raw target preserves exact bytes of qcow2 and ISO imports.
            volume = self.pool.createXML(
                volume_xml(name, source.stat().st_size, "raw", description=description),
                0,
            )
            self.remember(volume, digest)
            stream = self.conn.newStream(0)
            try:
                volume.upload(stream, 0, source.stat().st_size, 0)
                with source.open("rb") as f:
                    stream.sendAll(lambda _s, length, _o: f.read(length), None)
                stream.finish()
                if self._digest(volume) != digest:
                    raise ValueError(f"Volume checksum verification failed: {name}")
            except BaseException:
                with suppress(Exception):
                    stream.abort()
                with suppress(Exception):
                    volume.delete(0)
                raise
            return Path(volume.path())

    def disk(self, name: str, size_gb: int, backing: Path | None = None) -> Path:
        if self.lookup(name) is not None:
            raise ValueError(f"Volume {name} already exists; refusing to overwrite")
        volume = self.pool.createXML(
            volume_xml(
                name,
                size_gb * 1024**3,
                "qcow2",
                backing=str(backing) if backing else None,
                description=f"cabritactl:cluster:{self.cluster}",
            ),
            0,
        )
        self.remember(volume)
        return Path(volume.path())

    def remove(self, name: str) -> None:
        volume = self.lookup(name)
        if volume is None:
            return
        if not self.owns(volume) or not name.startswith(f"cabrita-{self.cluster}-node"):
            raise ValueError(f"Refusing to delete unowned volume {name}")
        volume.delete(0)

    def export_chain(self, name: str, directory: Path) -> Path:
        """Export disk plus backing files, rewriting only the temporary copy."""
        import subprocess

        volume = self.lookup(name)
        if volume is None:
            raise ValueError(f"Missing managed volume: {name}")
        destination = directory / name
        self.download(volume, destination)
        info = json.loads(
            subprocess.check_output(
                ["qemu-img", "info", "--output=json", str(destination)], text=True
            )
        )
        backing = info.get("full-backing-filename") or info.get("backing-filename")
        if backing:
            base = next(
                (v for v in self.pool.listAllVolumes() if v.path() == backing), None
            )
            if base is None:
                raise ValueError("Backing file is outside the managed pool")
            local_base = directory / f"{uuid.uuid4().hex}.qcow2"
            self.download(base, local_base)
            subprocess.run(
                [
                    "qemu-img",
                    "rebase",
                    "-u",
                    "-f",
                    "qcow2",
                    "-F",
                    "qcow2",
                    "-b",
                    str(local_base),
                    str(destination),
                ],
                check=True,
                capture_output=True,
            )
        return destination
