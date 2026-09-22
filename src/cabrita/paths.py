from pathlib import Path

from platformdirs import PlatformDirs

dirs = PlatformDirs(appname="cabrita", appauthor=False)


def get_cache_dir() -> Path:
    """Returns XDG cache directory (~/.cache/cabrita)."""
    p = dirs.user_cache_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_state_dir() -> Path:
    """Returns XDG state directory (~/.local/state/cabrita)."""
    p = dirs.user_state_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_golden_image_dir() -> Path:
    """Returns directory for golden base images (~/.cache/cabrita/golden)."""
    p = get_cache_dir() / "golden"
    p.mkdir(parents=True, exist_ok=True)
    return p
