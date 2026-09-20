"""Bastion HTTP Range server re-exports and utility functions."""

import socket
from typing import Any


def is_running_on_bastion(bastion_hostname: str = "carlanga") -> bool:
    """Returns True if the current process is executing directly on the bastion host."""
    try:
        return socket.gethostname().lower() == bastion_hostname.lower()
    except OSError:
        return False


try:
    from scc_provider_helvetios.media_server import EphemeralRangeHTTPServer
except ImportError:
    EphemeralRangeHTTPServer: Any = None

__all__ = ["EphemeralRangeHTTPServer", "is_running_on_bastion"]
