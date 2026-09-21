import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from cabrita.config import ClusterSettings
from cabrita.paths import get_image_cache_dir

__all__ = [
    "ensure_cached_cloud_image",
    "is_qcow2_image",
]

console = Console()


def ensure_cached_cloud_image(
    settings: ClusterSettings,
    image_source: str | None = None,
) -> Path:
    """Ensures Rocky Linux cloud qcow2 base image is cached internally.

    Accepts either a local file path (or file:// URI) or an HTTP/HTTPS URL.
    Uses platformdirs XDG cache directory: ~/.cache/cabrita/images/<image_name>.
    """
    source = image_source or settings.cloud_image_source
    cache_dir = get_image_cache_dir()

    is_url = source.startswith(("http://", "https://", "ftp://"))
    image_name = Path(source).name if not is_url else settings.cloud_image_name
    cache_path = cache_dir / image_name

    if is_url:
        if not cache_path.exists() or cache_path.stat().st_size < 50_000_000:
            console.print(
                f"[cyan]Downloading {image_name} from {source} to internal cache...[/cyan]"
            )
            subprocess.run(
                ["curl", "-L", "-o", str(cache_path), source], check=True, timeout=1800
            )
    else:
        file_str = source.removeprefix("file://")
        file_path = Path(file_str).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(
                f"Specified cloud image source file not found: {file_path}"
            )

        if file_path == cache_path or file_path.name == "golden-rocky-base.qcow2":
            return file_path

        needs_copy = (
            not cache_path.exists()
            or cache_path.stat().st_size != file_path.stat().st_size
            or file_path.stat().st_mtime > cache_path.stat().st_mtime
        )
        if needs_copy:
            console.print(
                f"[cyan]Caching cloud image from local file [bold]{file_path}[/bold] into [bold]{cache_path}[/bold]...[/cyan]"
            )
            shutil.copyfile(file_path, cache_path)

    try:
        cache_path.chmod(0o644)
    except OSError:
        pass

    return cache_path


def is_qcow2_image(path_or_url: str | Path) -> bool:
    """Returns True if the path or URL indicates a qcow2 cloud image."""
    s = str(path_or_url).lower()
    return ".qcow2" in s
