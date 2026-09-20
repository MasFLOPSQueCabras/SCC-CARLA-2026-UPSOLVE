"""Bastion HTTP Range server re-exports.

Consolidates under `scc_provider_helvetios.media_server` to eliminate code duplication.
"""

from scc_provider_helvetios.media_server import (
    EphemeralRangeHTTPServer,
    is_running_on_bastion,
)

__all__ = ["EphemeralRangeHTTPServer", "is_running_on_bastion"]
