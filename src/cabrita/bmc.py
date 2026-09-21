"""HPE iLO BMC Controller re-export.

Consolidates under `cabrita.providers.helvetios.bmc_client` to eliminate code duplication
and avoid importing httpx2 when the helvetios provider is not requested.
"""

from typing import Any

try:
    from cabrita.providers.helvetios.bmc_client import BMCController, SSHSocksTunnel
except ImportError:
    BMCController: Any = None
    SSHSocksTunnel: Any = None

__all__ = ["BMCController", "SSHSocksTunnel"]
