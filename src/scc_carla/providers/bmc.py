from pathlib import Path
from typing import Any

from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.providers.base import NodeProvider, PowerState


class BMCProvider(NodeProvider):
    """Bare-metal node provider utilizing HPE iLO 5 Redfish APIs."""

    def __init__(self, settings: ClusterSettings) -> None:
        self.settings = settings
        self.bmc = BMCController(settings)

    def power_on(self, node_id: int) -> bool:
        return self.bmc.power_on(node_id)

    def power_off(self, node_id: int, graceful: bool = True) -> bool:
        return self.bmc.power_off(node_id, graceful=graceful)

    def power_reset(self, node_id: int, graceful: bool = True) -> bool:
        return self.bmc.reset(node_id, graceful=graceful)

    def get_power_status(self, node_id: int) -> PowerState:
        status_str = self.bmc.get_power_status(node_id)
        match status_str:
            case "ON":
                return PowerState.ON
            case "OFF":
                return PowerState.OFF
            case "RESTARTING":
                return PowerState.RESTARTING
            case _:
                return PowerState.UNKNOWN

    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        return self.bmc.get_power_metrics(node_id)

    def provision_node(
        self,
        node_id: int,
        ks_cfg_path: Path,
        pubkey: str,
        bios_profile: str = "hpc",
        iso_url: str = "",
        oemdrv_url: str = "",
        **kwargs: Any,
    ) -> bool:
        return self.bmc.mount_and_boot(node_id, iso_url=iso_url, floppy_url=oemdrv_url)

    def teardown_node(self, node_id: int) -> bool:
        self.bmc.eject_virtual_media(node_id)
        return self.bmc.power_off(node_id, graceful=False)

    def get_bios_settings(self, node_id: int) -> dict[str, Any]:
        return self.bmc.get_bios_settings(node_id)

    def set_bios_settings(self, node_id: int, attributes: dict[str, Any]) -> bool:
        return self.bmc.set_bios_settings(node_id, attributes)

    def eject_virtual_media(self, node_id: int) -> bool:
        return self.bmc.eject_virtual_media(node_id)

    def close(self) -> None:
        self.bmc.close()
