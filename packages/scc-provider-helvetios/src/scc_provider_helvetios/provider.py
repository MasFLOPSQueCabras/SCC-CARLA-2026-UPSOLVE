from pathlib import Path
from typing import Any

from scc_core.manifest.models import ClusterManifest, NodeSpec
from scc_core.providers.base import NodeProvider, PowerState, ProviderPaths

from scc_provider_helvetios.bmc_client import BMCController
from scc_provider_helvetios.media_server import is_running_on_bastion


class HelvetiosBMCProvider(NodeProvider):
    """Bare-metal node provider utilizing HPE iLO 5 Redfish APIs and unprivileged bastion transport."""

    def __init__(
        self,
        manifest: ClusterManifest | None = None,
        paths: ProviderPaths | None = None,
        bastion_ssh_host: str = "scc-bastion",
        bastion_hostname: str = "carlanga",
        bmc_user: str = "",
        bmc_password: str = "",
    ) -> None:
        self.manifest = manifest
        self.bastion_ssh_host = (
            manifest.bastion.ssh_host if manifest else bastion_ssh_host
        )
        self.bastion_hostname = bastion_hostname
        self.bmc = BMCController(
            manifest=manifest,
            bastion_ssh_host=self.bastion_ssh_host,
            bastion_hostname=bastion_hostname,
            bmc_user=bmc_user,
            bmc_password=bmc_password,
        )

        on_bastion = is_running_on_bastion(self.bastion_hostname)
        remote_serve = manifest.bastion.remote_serve_dir if manifest else "~/scc_serve"
        gateway = manifest.network.gateway if manifest else "10.2.72.254"
        dns = manifest.network.dns if manifest else "10.2.72.254"

        self._paths = paths or ProviderPaths(
            staging_dir=Path.home() / ".cache" / "scc_carla" / "staging",
            iso_cache_dir=Path.home() / ".cache" / "scc_carla" / "iso",
            storage_dir=None,
            state_db_path=Path.home() / ".config" / "scc_carla" / "state.db",
            gateway_ip=gateway,
            dns_ip=dns,
            remote_serve_dir=str(Path(remote_serve).expanduser()),
            bastion_ssh_host=None if on_bastion else self.bastion_ssh_host,
        )

    @property
    def name(self) -> str:
        return "helvetios"

    @property
    def paths(self) -> ProviderPaths:
        return self._paths

    def _get_node_spec(self, node_id: int) -> NodeSpec | None:
        if self.manifest:
            for n in self.manifest.nodes:
                if n.id == node_id:
                    return n
        return None

    def get_node_ip(self, node_id: int) -> str:
        spec = self._get_node_spec(node_id)
        if spec:
            return spec.ip
        return f"10.2.72.{node_id}"

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

    def close(self) -> None:
        self.bmc.close()
