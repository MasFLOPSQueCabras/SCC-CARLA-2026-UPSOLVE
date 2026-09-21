"""Libvirt virtualization provider re-export."""

from typing import Any

try:
    from cabrita.providers.libvirt_backend.provider import LibvirtProvider
except ImportError:
    LibvirtProvider: Any = None

__all__ = ["LibvirtProvider"]
