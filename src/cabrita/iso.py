import mmap
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from cabrita.config import ClusterSettings
from cabrita.paths import get_iso_cache_dir

console = Console()


def patch_iso_in_place(iso_path: Path) -> None:
    """Patches GRUB configuration in Rocky Linux ISO for automated direct-boot.

    Changes set default="1" to set default="0" and set timeout=60 to set timeout=02.
    """
    try:
        with open(iso_path, "r+b") as f, mmap.mmap(f.fileno(), 0) as mm:
            start = 0
            while True:
                idx = mm.find(b'set default="1"', start)
                if idx == -1:
                    break
                mm[idx : idx + 15] = b'set default="0"'
                start = idx + 15

            start = 0
            while True:
                idx = mm.find(b"set timeout=60", start)
                if idx == -1:
                    break
                mm[idx : idx + 14] = b"set timeout=02"
                start = idx + 14
            mm.flush()
    except PermissionError, OSError:
        # Already patched or owned by hypervisor service (e.g. qemu:qemu)
        pass


def ensure_cached_iso(
    settings: ClusterSettings,
    iso_source: str | None = None,
) -> Path:
    """Ensures Rocky Linux ISO is cached internally and patched for direct-boot.

    Accepts either a local file path (or file:// URI) or an HTTP/HTTPS URL.
    Uses platformdirs XDG cache directory: ~/.cache/cabrita/iso/<iso_name>.
    """
    source = iso_source or settings.iso_source
    cache_dir = get_iso_cache_dir()
    cache_path = cache_dir / settings.iso_name

    # Determine if source is a URL or a local file
    is_url = source.startswith(("http://", "https://", "ftp://"))

    if is_url:
        if not cache_path.exists() or cache_path.stat().st_size < 100_000_000:
            console.print(
                f"[cyan]Downloading {settings.iso_name} from {source} to internal cache...[/cyan]"
            )
            subprocess.run(
                ["curl", "-L", "-o", str(cache_path), source], check=True, timeout=1800
            )
            patch_iso_in_place(cache_path)
        else:
            patch_iso_in_place(cache_path)
    else:
        # Local file source
        file_str = source.removeprefix("file://")
        file_path = Path(file_str).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Specified ISO source file not found: {file_path}")

        # Check if cache is missing or older/different from source
        needs_copy = (
            not cache_path.exists()
            or cache_path.stat().st_size != file_path.stat().st_size
            or file_path.stat().st_mtime > cache_path.stat().st_mtime
        )
        if needs_copy and file_path != cache_path:
            console.print(
                f"[cyan]Caching ISO from local file [bold]{file_path}[/bold] into [bold]{cache_path}[/bold]...[/cyan]"
            )
            shutil.copyfile(file_path, cache_path)
            patch_iso_in_place(cache_path)
        else:
            patch_iso_in_place(cache_path)

    return cache_path
