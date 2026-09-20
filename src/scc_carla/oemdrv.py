"""OEMDRV generation re-exports.

Consolidates under `scc_provider_helvetios.oemdrv` to eliminate code duplication.
"""

from scc_provider_helvetios.oemdrv import generate_oemdrv

__all__ = ["generate_oemdrv"]
