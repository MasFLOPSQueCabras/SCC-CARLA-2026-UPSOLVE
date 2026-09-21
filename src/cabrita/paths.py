from pathlib import Path

from platformdirs import PlatformDirs

dirs = PlatformDirs(appname="cabrita", appauthor=False)


def get_cache_dir() -> Path:
    """Returns XDG cache directory (~/.cache/cabrita)."""
    p = dirs.user_cache_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_dir() -> Path:
    """Returns XDG config directory (~/.config/cabrita)."""
    p = dirs.user_config_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_state_dir() -> Path:
    """Returns XDG state directory (~/.local/state/cabrita)."""
    p = dirs.user_state_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_data_dir() -> Path:
    """Returns XDG data directory (~/.local/share/cabrita)."""
    p = dirs.user_data_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_iso_cache_dir() -> Path:
    """Returns directory for cached ISO images (~/.cache/cabrita/iso)."""
    p = get_cache_dir() / "iso"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_image_cache_dir() -> Path:
    """Returns directory for cached base cloud images (~/.cache/cabrita/images)."""
    p = get_cache_dir() / "images"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_staging_dir() -> Path:
    """Returns directory for kickstart/media staging (~/.cache/cabrita/staging)."""
    p = get_cache_dir() / "staging"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_libvirt_storage_dir() -> Path:
    """Returns directory for libvirt VM disks (~/.cache/cabrita/libvirt)."""
    p = get_cache_dir() / "libvirt"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_golden_image_dir() -> Path:
    """Returns directory for golden base images (~/.cache/cabrita/golden)."""
    p = get_cache_dir() / "golden"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_local_db_path() -> Path:
    """Returns Cabrita's state database without adopting legacy state."""
    return get_state_dir() / "local_state.db"
