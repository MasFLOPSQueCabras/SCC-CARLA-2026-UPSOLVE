from pathlib import Path

from platformdirs import PlatformDirs

dirs = PlatformDirs(appname="scc_carla", appauthor=False)


def get_cache_dir() -> Path:
    """Returns XDG cache directory (~/.cache/scc_carla)."""
    p = dirs.user_cache_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_dir() -> Path:
    """Returns XDG config directory (~/.config/scc_carla)."""
    p = dirs.user_config_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_state_dir() -> Path:
    """Returns XDG state directory (~/.local/state/scc_carla)."""
    p = dirs.user_state_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_data_dir() -> Path:
    """Returns XDG data directory (~/.local/share/scc_carla)."""
    p = dirs.user_data_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_iso_cache_dir() -> Path:
    """Returns directory for cached ISO images (~/.cache/scc_carla/iso)."""
    p = get_cache_dir() / "iso"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_image_cache_dir() -> Path:
    """Returns directory for cached base cloud images (~/.cache/scc_carla/images)."""
    p = get_cache_dir() / "images"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_staging_dir() -> Path:
    """Returns directory for kickstart/media staging (~/.cache/scc_carla/staging)."""
    p = get_cache_dir() / "staging"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_libvirt_storage_dir() -> Path:
    """Returns directory for libvirt VM disks (~/.cache/scc_carla/libvirt)."""
    p = get_cache_dir() / "libvirt"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_local_db_path() -> Path:
    """Returns local SQLite database path (~/.local/state/scc_carla/local_state.db).

    Falls back to legacy ~/.config/scc_carla/local_state.db if it exists.
    """
    legacy_path = get_config_dir() / "local_state.db"
    if legacy_path.exists():
        return legacy_path
    return get_state_dir() / "local_state.db"
