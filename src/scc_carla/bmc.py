"""HPE iLO BMC Controller re-export.

Consolidates under `scc_provider_helvetios.bmc_client` to eliminate code duplication
and avoid importing httpx2 when the helvetios provider is not requested.
"""

from typing import Any

try:
    from scc_provider_helvetios.bmc_client import BMCController, SSHSocksTunnel
except ImportError:
    BMCController: Any = None
    SSHSocksTunnel: Any = None

__all__ = ["BMCController", "SSHSocksTunnel"]
