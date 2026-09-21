import importlib.resources as ir
import logging
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from cabrita.config import ClusterSettings
from cabrita.core.manifest.models import ClusterManifest, NodeSpec
from cabrita.core.oemdrv import generate_oemdrv
from cabrita.core.providers.base import NodeProvider, PowerState, ProviderPaths
from cabrita.core.templating import TemplateEngine
from cabrita.providers.helvetios.bios import BiosProfile, get_profile_attributes
from cabrita.providers.helvetios.bmc_client import BMCController
from cabrita.providers.helvetios.media_server import (
    EphemeralRangeHTTPServer,
    is_running_on_bastion,
)


class HelvetiosProvider(NodeProvider):
    """Bare-metal node provider utilizing HPE iLO 5 Redfish APIs and unprivileged bastion transport."""

    def __init__(
        self,
        manifest: ClusterManifest | None = None,
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
            self.bastion_ssh_host = (
                manifest.bastion.ssh_host if manifest else bastion_ssh_host
            )
            self.bastion_hostname = bastion_hostname
            self.http_port = http_port
            self.http_ip = http_ip
            remote_serve = (
                manifest.bastion.remote_serve_dir if manifest else "~/cabrita_serve"
            )
            gateway = manifest.network.gateway if manifest else "10.2.72.254"
            dns = manifest.network.dns if manifest else "10.2.72.254"
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
            remote_serve_dir=str(Path(remote_serve).expanduser()),
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

    @classmethod
    def get_preset_config(cls, profile: str = "hpc") -> str:
        ref = ir.files("cabrita.providers.helvetios").joinpath(
            "configs", "helvetios-hpc.yaml"
        )
        return ref.read_text(encoding="utf-8")

    @classmethod
    def get_templates_dir(cls) -> Path | None:
        try:
            ref = ir.files("cabrita.providers.helvetios").joinpath("templates")
            with ir.as_file(ref) as p:
                if p.is_dir():
                    return Path(p)
        except ModuleNotFoundError, TypeError, FileNotFoundError:
            pass
        return None

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
        if spec:
            return spec.ip
        if self.settings and hasattr(self.settings, "get_node_ip"):
            return self.settings.get_node_ip(node_id)
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

    def get_bios_settings(self, node_id: int) -> dict[str, Any]:
        return self.bmc.get_bios_settings(node_id)

    def set_bios_settings(self, node_id: int, attributes: dict[str, Any]) -> bool:
        return self.bmc.set_bios_settings(node_id, attributes)

    def _ensure_bastion_iso(
        self,
        template_engine: TemplateEngine,
        iso_source: str | None = None,
    ) -> None:
        """Ensures the Rocky Linux minimal ISO is available on the Bastion HTTP serve directory."""
        serve_path = (
            Path(self.paths.remote_serve_dir)
            if self.paths.remote_serve_dir
            else (Path.home() / "cabrita_serve")
        )
        on_bastion = is_running_on_bastion(self.bastion_hostname)
        iso_name = "Rocky-10.2-x86_64-minimal.iso"
        if (
            self.manifest
            and self.manifest.defaults
            and self.manifest.defaults.os.iso_name
        ):
            iso_name = self.manifest.defaults.os.iso_name
        elif self.settings and hasattr(self.settings, "iso_name"):
            iso_name = self.settings.iso_name

        if on_bastion:
            dest_iso_path = serve_path / iso_name
            if not dest_iso_path.exists():
                dest_iso_path.parent.mkdir(parents=True, exist_ok=True)
                cached = self.paths.iso_cache_dir / iso_name
                if cached.exists():
                    subprocess.run(
                        ["cp", str(cached), str(dest_iso_path)],
                        check=True,
                        timeout=1800,
                    )
            return

        check_script = template_engine.render(
            "scripts/bastion_check_iso.sh.j2",
            {"serve_dir": str(serve_path), "iso_name": iso_name},
        )
        try:
            res = subprocess.run(
                ["ssh", self.bastion_ssh_host, "bash -s"],
                input=check_script,
                capture_output=True,
                text=True,
                check=True,
                timeout=1800,
            )
            status = res.stdout.strip()
        except subprocess.CalledProcessError, OSError:
            status = "UNKNOWN"

        if status == "MISSING":
            cached = self.paths.iso_cache_dir / iso_name
            if cached.exists():
                subprocess.run(
                    [
                        "scp",
                        str(cached),
                        f"{self.bastion_ssh_host}:{serve_path}/{iso_name}",
                    ],
                    check=True,
                    timeout=1800,
                )

    @contextmanager
    def deployment_session(self) -> Generator[None]:
        """Runs the ephemeral Range HTTP server for HPE iLO virtual media boot."""
        te = self.template_engine or TemplateEngine()
        self._ensure_bastion_iso(template_engine=te)
        server = EphemeralRangeHTTPServer(
            port=self.http_port,
            bind_ip=self.http_ip,
            bastion_ssh_host=self.bastion_ssh_host,
            bastion_hostname=self.bastion_hostname,
            template_engine=te,
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
        te = template_engine or self.template_engine
        stg = staging_dir or self.paths.staging_dir
        stg.mkdir(parents=True, exist_ok=True)

        spec = self._get_node_spec(node_id)
        hostname = (
            spec.hostname
            if spec
            else (
                self.settings.get_hostname(node_id)
                if self.settings
                else f"node{node_id}"
            )
        )
        node_ip = self.get_node_ip(node_id)

        # 1. Staging Kickstart
        if progress_callback:
            progress_callback("Staging Kickstart configuration...")
        ks_cfg_path = stg / f"ks_node{node_id}.cfg"
        username = (
            self.manifest.defaults.os.username
            if (self.manifest and self.manifest.defaults)
            else (self.settings.node_username if self.settings else "scct-2672")
        )
        target_disk = None
        if self.manifest and self.manifest.defaults and self.manifest.defaults.hardware:
            target_disk = self.manifest.defaults.hardware.target_disk

        context = {
            "node_ip": node_ip,
            "gateway_ip": self.paths.gateway_ip,
            "dns_ip": self.paths.dns_ip,
            "hostname": hostname,
            "node_username": username,
            "pubkey": pubkey,
            "target_disk": target_disk,
        }
        te.render_to_file("kickstart/ks.cfg.j2", context, ks_cfg_path)

        # 2. Generate OEMDRV FAT image
        if progress_callback:
            progress_callback("Generating OEMDRV FAT boot volume...")
        oemdrv_name = f"oemdrv_node{node_id}.img"
        oemdrv_path = stg / oemdrv_name
        generate_oemdrv(ks_cfg_path, oemdrv_path, template_engine=te)

        # 3. Synchronize OEMDRV to Bastion HTTP serving directory
        serve_path = (
            Path(self.paths.remote_serve_dir)
            if self.paths.remote_serve_dir
            else (Path.home() / "cabrita_serve")
        )
        on_bastion = is_running_on_bastion(self.bastion_hostname)
        if on_bastion:
            dest_path = serve_path / oemdrv_name
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(oemdrv_path.read_bytes())
        else:
            subprocess.run(
                [
                    "scp",
                    "-q",
                    str(oemdrv_path),
                    f"{self.bastion_ssh_host}:{serve_path}/{oemdrv_name}",
                ],
                check=True,
                timeout=1800,
            )

        # 4. Set BIOS profile
        if progress_callback:
            progress_callback(f"Configuring BIOS '{bios_profile}' profile...")
        try:
            profile_enum = BiosProfile(bios_profile.lower())
            bios_attrs = get_profile_attributes(profile_enum)
            self.set_bios_settings(node_id, bios_attrs)
        except (ValueError, KeyError, AttributeError, RuntimeError) as exc:
            logger.debug(
                "Could not apply BIOS profile '%s' on node %s: %s",
                bios_profile,
                node_id,
                exc,
            )

        # 5. Mount Virtual Media & trigger boot
        if progress_callback:
            progress_callback("Mounting Virtual Media & triggering boot...")
        iso_name = "Rocky-10.2-x86_64-minimal.iso"
        if (
            self.manifest
            and self.manifest.defaults
            and self.manifest.defaults.os.iso_name
        ):
            iso_name = self.manifest.defaults.os.iso_name
        elif self.settings and hasattr(self.settings, "iso_name"):
            iso_name = self.settings.iso_name

        iso_url = f"http://{self.http_ip}:{self.http_port}/{iso_name}"
        oemdrv_url = f"http://{self.http_ip}:{self.http_port}/{oemdrv_name}"

        return self.bmc.mount_and_boot(node_id, iso_url=iso_url, floppy_url=oemdrv_url)

    def post_provision(self, node_id: int) -> None:
        """Ejects virtual media once OS installation is complete and SSH is responsive."""
        self.bmc.eject_virtual_media(node_id)

    def teardown_node(self, node_id: int) -> bool:
        self.bmc.eject_virtual_media(node_id)
        return self.bmc.power_off(node_id, graceful=False)

    def close(self) -> None:
        self.bmc.close()


# Backward compatibility alias
HelvetiosBMCProvider = HelvetiosProvider
