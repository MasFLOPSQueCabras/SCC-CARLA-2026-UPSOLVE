"""Golden Image caching, format conversion, and compression utilities."""

import json
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

    result = subprocess.run(
        ["qemu-img", "info", "--output=json", str(p)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    return ImageMetadata(
        path=p,
        format=data["format"],
        virtual_size_bytes=int(data["virtual-size"]),
        actual_size_bytes=int(data.get("actual-size", p.stat().st_size)),
        compressed=bool(data.get("compressed", False)),
    )
