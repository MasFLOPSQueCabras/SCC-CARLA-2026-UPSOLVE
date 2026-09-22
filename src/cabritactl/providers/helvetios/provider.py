import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from cabritactl.bootstrap.http_server import (
    EphemeralRangeHTTPServer,
    is_running_on_bastion,
)
from cabritactl.config import ClusterSettings
from cabritactl.core.manifest.models import ClusterManifest, NodeSpec
from cabritactl.core.providers.base import NodeProvider, PowerState, ProviderPaths
from cabritactl.core.templating import TemplateEngine
from cabritactl.providers.helvetios.bmc_client import BMCController


class HelvetiosProvider(NodeProvider):
    """Bare-metal node provider utilizing HPE iLO 5 Redfish APIs and unprivileged bastion transport."""

    def __init__(
        self,
        manifest: ClusterManifest,
        paths: ProviderPaths | None = None,
        bastion_ssh_host: str = "cabrita-bastion",
        bastion_hostname: str = "carlanga",
        bmc_user: str = "",
        bmc_password: str = "",
        http_port: int = 8072,
        http_ip: str = "10.2.72.254",
        template_engine: TemplateEngine | None = None,
        settings: ClusterSettings | None = None,
    ) -> None:
        self.manifest = manifest
        self.settings = settings
        self.template_engine = template_engine or TemplateEngine()

        if settings is not None:
            self.bastion_ssh_host = settings.bastion_ssh_host
            self.bastion_hostname = settings.bastion_hostname
            self.http_port = settings.bastion_http_port
            self.http_ip = settings.bastion_http_ip
            bmc_user = settings.bmc_user
            bmc_password = settings.bmc_password
            remote_serve = settings.bastion_serve_dir
            gateway = settings.gateway_ip
            dns = settings.dns_ip
            state_db = settings.bastion_state_db_path
        else:
            self.bastion_ssh_host = manifest.bastion.ssh_host
            self.bastion_hostname = bastion_hostname
            self.http_port = http_port
            self.http_ip = http_ip
            remote_serve = manifest.bastion.remote_serve_dir
            gateway = manifest.network.gateway
            dns = manifest.network.dns
            state_db = Path.home() / ".config" / "cabrita" / "state.db"

        self.bmc = BMCController(
            manifest=manifest,
            bastion_ssh_host=self.bastion_ssh_host,
            bastion_hostname=self.bastion_hostname,
            bmc_user=bmc_user,
            bmc_password=bmc_password,
        )

        on_bastion = is_running_on_bastion(self.bastion_hostname)
        self._paths = paths or ProviderPaths(
            staging_dir=Path.home() / ".cache" / "cabrita" / "staging",
            iso_cache_dir=Path.home() / ".cache" / "cabrita" / "iso",
            storage_dir=None,
            state_db_path=state_db,
            gateway_ip=gateway,
            dns_ip=dns,
            remote_serve_dir=remote_serve,
            bastion_ssh_host=None if on_bastion else self.bastion_ssh_host,
        )

    @property
    def name(self) -> str:
        return "helvetios"

    @property
    def paths(self) -> ProviderPaths:
        return self._paths

    @classmethod
    def list_presets(cls) -> list[str]:
        return ["hpc"]

    def _get_node_spec(self, node_id: int) -> NodeSpec | None:
        if self.manifest:
            for n in self.manifest.nodes:
                if n.id == node_id:
                    return n
        return None

    def node_exists(self, node_id: int) -> bool:
        return self.get_power_status(node_id) != PowerState.UNKNOWN

    def get_node_ip(self, node_id: int) -> str:
        spec = self._get_node_spec(node_id)
        if spec is None:
            raise ValueError(f"Undeclared node: {node_id}")
        return spec.ip

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

    def get_bios_settings(self, node_id: int) -> dict[str, Any]:
        return self.bmc.get_bios_settings(node_id)

    def set_bios_settings(self, node_id: int, attributes: dict[str, Any]) -> bool:
        return self.bmc.set_bios_settings(node_id, attributes)

    @contextmanager
    def deployment_session(self) -> Generator[None]:
        """Runs the ephemeral Range HTTP server for HPE iLO virtual media boot."""
        server = EphemeralRangeHTTPServer(
            port=self.http_port,
            bind_ip=self.http_ip,
            bastion_ssh_host=self.bastion_ssh_host,
            bastion_hostname=self.bastion_hostname,
            remote_serve_dir=self.paths.remote_serve_dir,
        )
        with server:
            yield

    def provision_node(
        self,
        node_id: int,
        pubkey: str,
        bios_profile: str = "hpc",
        image_source: str | None = None,
        template_engine: Any | None = None,
        staging_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> bool:
        if image_source is None or not image_source.startswith(("http://", "https://")):
            raise ValueError(
                "Helvetios requires explicitly prepared HTTP installation media"
            )
        return self.bmc.mount_and_boot(
            node_id, iso_url=image_source, floppy_url=kwargs.get("oemdrv_path")
        )

    def post_provision(self, node_id: int) -> None:
        """Ejects virtual media once OS installation is complete and SSH is responsive."""
        if not self.bmc.eject_virtual_media(node_id):
            raise RuntimeError(f"Failed to detach media on node {node_id}")
        if not self.bmc.power_on(node_id):
            raise RuntimeError(f"Failed to boot disk on node {node_id}")

    def teardown_node(self, node_id: int) -> bool:
        if not self.bmc.eject_virtual_media(node_id):
            raise RuntimeError(f"Failed to detach media on node {node_id}")
        return self.bmc.power_off(node_id, graceful=False)

    def close(self) -> None:
        self.bmc.close()
