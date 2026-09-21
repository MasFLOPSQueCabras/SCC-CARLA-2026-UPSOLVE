"""Golden Image caching, format conversion, and compression utilities."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImageMetadata:
    path: Path
    format: str
    virtual_size_bytes: int
    actual_size_bytes: int
    compressed: bool = False


def inspect_image(image_path: Path) -> ImageMetadata:
    """Inspects an image using qemu-img info (JSON output) or file stats."""
    p = image_path.expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")

    if p.suffix == ".zst":
        return ImageMetadata(
            path=p,
            format="zstd-compressed",
            virtual_size_bytes=p.stat().st_size,
            actual_size_bytes=p.stat().st_size,
            compressed=True,
        )

    if shutil.which("qemu-img"):
        try:
            res = subprocess.run(
                ["qemu-img", "info", "--output=json", str(p)],
                capture_output=True,
                text=True,
                check=True,
                timeout=1800,
            )
            data = json.loads(res.stdout)
            return ImageMetadata(
                path=p,
                format=data.get("format", "unknown"),
                virtual_size_bytes=int(data.get("virtual-size", 0)),
                actual_size_bytes=int(data.get("actual-size", p.stat().st_size)),
                compressed=bool(data.get("compressed", False)),
            )
        except subprocess.CalledProcessError, json.JSONDecodeError:
            pass

    return ImageMetadata(
        path=p,
        format=p.suffix.lstrip("."),
        virtual_size_bytes=p.stat().st_size,
        actual_size_bytes=p.stat().st_size,
        compressed=False,
    )


def convert_qcow2_to_raw(qcow2_path: Path, raw_path: Path) -> Path:
    """Converts a QCOW2 image to raw format using qemu-img."""
    src = qcow2_path.expanduser().resolve()
    dst = raw_path.expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not shutil.which("qemu-img"):
        raise RuntimeError("qemu-img utility is required for image conversion.")

    subprocess.run(
        ["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(src), str(dst)],
        check=True,
        timeout=1800,
    )
    return dst


def compress_zstd(source_path: Path, dest_path: Path, level: int = 6) -> Path:
    """Compresses a file using zstd."""
    src = source_path.expanduser().resolve()
    dst = dest_path.expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("zstd"):
        subprocess.run(
            ["zstd", f"-{level}", "-T0", "-f", str(src), "-o", str(dst)],
            check=True,
            timeout=1800,
        )
        return dst

    raise RuntimeError("zstd command-line utility is required for zstd compression.")
