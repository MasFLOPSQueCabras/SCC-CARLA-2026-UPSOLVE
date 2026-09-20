from typing import Any

try:
    from scc_provider_libvirt.cidata import generate_cidata
except ImportError:
    generate_cidata: Any = None

__all__ = ["generate_cidata"]
