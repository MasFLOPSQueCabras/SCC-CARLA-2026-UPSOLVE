import shutil
import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def is_qcow2_image(path: Path | str) -> bool:
    p = Path(path).expanduser().resolve()
    if not p.exists() or p.is_dir():
        return False
    try:
        with open(p, "rb") as f:
            magic = f.read(4)
            return magic == b"QFI\xfb"
    except OSError:
        return False


def ensure_cached_cloud_image(
    image_source: str,
    cache_dir: Path,
    image_name: str | None = None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    is_url = image_source.startswith(("http://", "https://", "ftp://"))
    target_name = image_name or (
        Path(image_source).name
        if not is_url
        else "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2"
    )
    cache_path = cache_dir / target_name

    if is_url:
        if not cache_path.exists() or cache_path.stat().st_size < 50_000_000:
            console.print(
                f"[cyan]Downloading {target_name} from {image_source} to internal cache...[/cyan]"
            )
            subprocess.run(
                ["curl", "-L", "-o", str(cache_path), image_source],
                check=True,
                timeout=1800,
            )
    else:
        file_str = image_source.removeprefix("file://")
        file_path = Path(file_str).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Cloud image source file not found: {file_path}")

        needs_copy = (
            not cache_path.exists()
            or cache_path.stat().st_size != file_path.stat().st_size
            or file_path.stat().st_mtime > cache_path.stat().st_mtime
        )
        if needs_copy and file_path != cache_path:
            console.print(
                f"[cyan]Caching cloud image from [bold]{file_path}[/bold] into [bold]{cache_path}[/bold]...[/cyan]"
            )
            shutil.copyfile(file_path, cache_path)

    try:
        cache_path.chmod(0o644)
    except OSError:
        pass
    return cache_path


def create_cow_overlay(
    base_image: Path,
    overlay_path: Path,
    size: str = "40G",
) -> Path:
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    if overlay_path.exists():
        overlay_path.unlink()

    cmd = [
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base_image.resolve()),
        str(overlay_path.resolve()),
        size,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
    try:
        overlay_path.chmod(0o666)
    except OSError:
        pass
    return overlay_path
