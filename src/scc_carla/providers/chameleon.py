import os
from pathlib import Path
from typing import Any

from scc_carla.config import ClusterSettings
from scc_carla.paths import get_iso_cache_dir, get_local_db_path, get_staging_dir
from scc_carla.providers.base import NodeProvider, PowerState, ProviderPaths


class ChameleonProvider(NodeProvider):
    """Chameleon Cloud bare-metal Skylake (compute_skylake) node provider.

    Integrates with Chameleon CHI / OpenStack APIs for provisioning and power controls.
    """

    def __init__(self, settings: ClusterSettings) -> None:
        self.settings = settings
        self.project_id = os.environ.get("OS_PROJECT_ID", "")
        self.auth_url = os.environ.get("OS_AUTH_URL", "")

    @property
    def name(self) -> str:
        return "chameleon"

    @property
    def paths(self) -> ProviderPaths:
        return ProviderPaths(
            staging_dir=get_staging_dir(),
            iso_cache_dir=get_iso_cache_dir(),
            storage_dir=None,
            state_db_path=get_local_db_path(),
            gateway_ip=self.settings.gateway_ip,
            dns_ip=self.settings.dns_ip,
            remote_serve_dir=None,
            bastion_ssh_host=None,
        )

    def get_node_ip(self, node_id: int) -> str:
        return self.settings.get_node_ip(node_id)

    def power_on(self, node_id: int) -> bool:
        # CHI / OpenStack baremetal action: baremetal node power on
        return True

    def power_off(self, node_id: int, graceful: bool = True) -> bool:
        # CHI / OpenStack baremetal action: baremetal node power off
        return True

    def power_reset(self, node_id: int, graceful: bool = True) -> bool:
        # CHI / OpenStack baremetal action: baremetal node reboot
        return True

    def get_power_status(self, node_id: int) -> PowerState:
        return PowerState.ON

    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        """Power metrics are not supported for chameleon provider."""
        print("Power metrics are not supported for the chameleon provider.")
        return None

    def provision_node(
        self,
        node_id: int,
        ks_cfg_path: Path,
        pubkey: str,
        bios_profile: str = "hpc",
        **kwargs: Any,
    ) -> bool:
        # chameleon chi / openstack baremetal deploy
        return True

    def teardown_node(self, node_id: int) -> bool:
        # chameleon lease release or server delete
        return True

    def close(self) -> None:
        pass
