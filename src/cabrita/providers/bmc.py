"""Helvetios bare-metal provider re-export."""

from typing import Any

try:
    from cabrita.providers.helvetios.provider import HelvetiosProvider

    BMCProvider = HelvetiosProvider
except ImportError:
    HelvetiosProvider: Any = None
    BMCProvider: Any = None

__all__ = ["BMCProvider", "HelvetiosProvider"]
