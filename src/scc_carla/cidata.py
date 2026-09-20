"""CIDATA generation re-exports.

Consolidates under `scc_provider_libvirt.cidata` to eliminate code duplication.
"""

from scc_provider_libvirt.cidata import generate_cidata

__all__ = ["generate_cidata"]
