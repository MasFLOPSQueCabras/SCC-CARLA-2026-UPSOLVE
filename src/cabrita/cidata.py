from typing import Any

try:
    from cabrita.providers.libvirt_backend.cidata import generate_cidata
except ImportError:
    generate_cidata: Any = None

__all__ = ["generate_cidata"]
