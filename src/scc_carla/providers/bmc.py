"""Helvetios bare-metal provider re-export."""

from __future__ import annotations

from scc_provider_helvetios.provider import HelvetiosProvider

# Backward compatibility alias
BMCProvider = HelvetiosProvider

__all__ = ["BMCProvider", "HelvetiosProvider"]
