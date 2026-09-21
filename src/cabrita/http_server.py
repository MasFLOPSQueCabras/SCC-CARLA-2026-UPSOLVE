"""Bastion HTTP Range server re-exports and utility functions."""

import socket


def is_running_on_bastion(bastion_hostname: str = "carlanga") -> bool:
    """Returns True if the current process is executing directly on the bastion host."""
    try:
        return socket.gethostname().lower() == bastion_hostname.lower()
    except OSError:
        return False
