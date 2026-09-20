import contextlib
import subprocess
from pathlib import Path
from typing import Any

import libvirt

from scc_carla.cidata import generate_cidata
from scc_carla.config import ClusterSettings
from scc_carla.image import create_cow_overlay, is_qcow2_image
from scc_carla.oemdrv import generate_oemdrv
from scc_carla.paths import (
    get_iso_cache_dir,
    get_libvirt_storage_dir,
    get_local_db_path,
    get_staging_dir,
)
from scc_carla.providers.base import NodeProvider, PowerState, ProviderPaths
from scc_carla.templating import TemplateEngine


class LibvirtProvider(NodeProvider):
    """Local virtualized node provider using libvirt-python and QEMU/KVM."""

    def __init__(self, settings: ClusterSettings) -> None:
        self.settings = settings
        self.uri = settings.libvirt_uri
        self.conn = libvirt.open(self.uri)
        if not self.conn:
            raise RuntimeError(f"Failed to connect to libvirt URI: {self.uri}")

        # Suppress default C-level stderr logging on benign lookup failures
        libvirt.registerErrorHandler(lambda ctx, err: None, None)
        self.storage_dir = get_libvirt_storage_dir()
        self.template_engine = TemplateEngine()

    @property
    def name(self) -> str:
        return "libvirt"

    @property
    def paths(self) -> ProviderPaths:
        return ProviderPaths(
            staging_dir=get_staging_dir(),
            iso_cache_dir=get_iso_cache_dir(),
            storage_dir=self.storage_dir,
            state_db_path=get_local_db_path(),
            gateway_ip="192.168.122.1",
            dns_ip="192.168.122.1",
            remote_serve_dir=None,
            bastion_ssh_host=None,
        )

    def get_node_ip(self, node_id: int) -> str:
        return f"192.168.122.10{node_id}"

    def _get_domain_name(self, node_id: int) -> str:
        return f"{self.settings.libvirt_domain_prefix}node{node_id}"

    def _get_domain(self, node_id: int) -> libvirt.virDomain | None:
        if self.conn is None:
            return None
        dom_name = self._get_domain_name(node_id)
        try:
            return self.conn.lookupByName(dom_name)
        except libvirt.libvirtError:
            return None

    def power_on(self, node_id: int) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            # If domain does not exist yet, provision it first
            return False

        try:
            state, _ = dom.state()
            if state == libvirt.VIR_DOMAIN_RUNNING:
                return True
            dom.create()
            return True
        except libvirt.libvirtError as e:
            print(f"Libvirt power_on error for node {node_id}: {e}")
            return False

    def power_off(self, node_id: int, graceful: bool = True) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            return True

        try:
            state, _ = dom.state()
            if state == libvirt.VIR_DOMAIN_SHUTOFF:
                return True

            if graceful:
                dom.shutdown()
            else:
                dom.destroy()
            return True
        except libvirt.libvirtError as e:
            print(f"Libvirt power_off error for node {node_id}: {e}")
            return False

    def power_reset(self, node_id: int, graceful: bool = True) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            return False

        try:
            if graceful:
                dom.reboot(libvirt.VIR_DOMAIN_REBOOT_DEFAULT)
            else:
                dom.reset()
            return True
        except libvirt.libvirtError as e:
            print(f"Libvirt power_reset error for node {node_id}: {e}")
            return False

    def get_power_status(self, node_id: int) -> PowerState:
        dom = self._get_domain(node_id)
        if dom is None:
            return PowerState.OFF

        try:
            state, _ = dom.state()
            match state:
                case libvirt.VIR_DOMAIN_RUNNING:
                    return PowerState.ON
                case libvirt.VIR_DOMAIN_SHUTDOWN:
                    return PowerState.RESTARTING
                case libvirt.VIR_DOMAIN_SHUTOFF:
                    return PowerState.OFF
                case _:
                    return PowerState.UNKNOWN
        except libvirt.libvirtError:
            return PowerState.UNKNOWN

    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        """Power metrics are not supported for virtualized libvirt domains."""
        print("Power metrics are not supported for the libvirt provider.")
        return None

    def _generate_domain_xml(
        self,
        node_id: int,
        dom_name: str,
        disk_path: Path,
        cdrom_path: Path | None = None,
        oemdrv_path: Path | None = None,
        memory_mib: int = 4096,
        vcpu: int = 2,
    ) -> str:
        context = {
            "dom_name": dom_name,
            "disk_path": str(disk_path.resolve()),
            "cdrom_path": (
                str(cdrom_path.resolve())
                if cdrom_path and cdrom_path.exists()
                else None
            ),
            "oemdrv_path": (
                str(oemdrv_path.resolve())
                if oemdrv_path and oemdrv_path.exists()
                else None
            ),
            "memory_mib": memory_mib,
            "vcpu": vcpu,
            "network": self.settings.libvirt_network,
            "mac_address": f"52:54:00:72:01:0{node_id}",
            "storage_dir": str(self.storage_dir.resolve()),
        }
        return self.template_engine.render("libvirt/domain.xml.j2", context)

    def provision_node(
        self,
        node_id: int,
        ks_cfg_path: Path,
        pubkey: str,
        bios_profile: str = "hpc",
        iso_path: Path | None = None,
        **kwargs: Any,
    ) -> bool:
        dom_name = self._get_domain_name(node_id)
        disk_path = self.storage_dir / f"{dom_name}.qcow2"

        # Ensure storage directory permissions for QEMU process
        try:
            self.storage_dir.chmod(0o777)
        except OSError:
            pass

        # If domain or old disk already exists, tear it down cleanly first
        dom = self._get_domain(node_id)
        if dom is not None:
            self.teardown_node(node_id)

        image_path: Path | None = kwargs.get("image_path")
        cloud_base_image: Path | None = None
        if image_path and is_qcow2_image(image_path):
            cloud_base_image = image_path
        elif iso_path and is_qcow2_image(iso_path):
            cloud_base_image = iso_path

        cdrom_path: Path | None = None
        aux_drive_path: Path | None = None

        if cloud_base_image is not None:
            # 1. Create QEMU COW overlay using the base cloud image
            create_cow_overlay(
                base_image=cloud_base_image,
                overlay_path=disk_path,
                size="25G",
            )

            # 2. Generate Cloud-Init CIDATA drive
            cidata_target = self.storage_dir / f"{dom_name}_cidata.img"
            user_data_path = self.paths.staging_dir / f"user_data_{dom_name}"
            meta_data_path = self.paths.staging_dir / f"meta_data_{dom_name}"
            network_config_path = self.paths.staging_dir / f"network_config_{dom_name}"

            ci_context = {
                "hostname": self.settings.get_hostname(node_id),
                "node_ip": self.get_node_ip(node_id),
                "gateway_ip": self.paths.gateway_ip,
                "dns_ip": self.paths.dns_ip,
                "mac_address": f"52:54:00:72:01:0{node_id}",
                "node_username": self.settings.node_username,
                "pubkey": pubkey,
            }
            self.template_engine.render_to_file(
                "cloudinit/user-data.j2", ci_context, user_data_path
            )
            self.template_engine.render_to_file(
                "cloudinit/meta-data.j2", ci_context, meta_data_path
            )
            self.template_engine.render_to_file(
                "cloudinit/network-config.j2", ci_context, network_config_path
            )

            generate_cidata(
                user_data_path=user_data_path,
                meta_data_path=meta_data_path,
                network_config_path=network_config_path,
                output_path=cidata_target,
                template_engine=self.template_engine,
            )
            aux_drive_path = cidata_target
            cdrom_path = None
        else:
            # Traditional Kickstart installer via ISO and OEMDRV
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk_path), "25G"],
                check=True,
                capture_output=True,
            )
            try:
                disk_path.chmod(0o666)
            except OSError:
                pass

            oemdrv_path: Path | None = kwargs.get("oemdrv_path")
            if oemdrv_path is None and ks_cfg_path.exists():
                oemdrv_target = self.storage_dir / f"{dom_name}_oemdrv.img"
                try:
                    generate_oemdrv(
                        ks_cfg_path,
                        oemdrv_target,
                        template_engine=self.template_engine,
                    )
                    oemdrv_path = oemdrv_target
                except subprocess.CalledProcessError, OSError:
                    oemdrv_path = None

            aux_drive_path = oemdrv_path
            cdrom_path = iso_path

        # 2. Define domain
        if self.conn is None:
            raise RuntimeError("Libvirt connection is closed")
        xml_desc = self._generate_domain_xml(
            node_id=node_id,
            dom_name=dom_name,
            disk_path=disk_path,
            cdrom_path=cdrom_path,
            oemdrv_path=aux_drive_path,
        )
        dom = self.conn.defineXML(xml_desc)
        if dom is None:
            raise RuntimeError(f"Failed to define domain {dom_name}")

        # 3. Start domain
        try:
            state, _ = dom.state()
            if state != libvirt.VIR_DOMAIN_RUNNING:
                dom.create()
            return True
        except libvirt.libvirtError as e:
            print(f"Failed to start libvirt domain {dom_name}: {e}")
            return False

    def teardown_node(self, node_id: int) -> bool:
        dom = self._get_domain(node_id)
        dom_name = self._get_domain_name(node_id)
        if dom is not None:
            try:
                state, _ = dom.state()
                if state != libvirt.VIR_DOMAIN_SHUTOFF:
                    dom.destroy()
                dom.undefine()
            except libvirt.libvirtError as e:
                print(f"Error tearing down domain {dom_name}: {e}")
                return False

        disk_path = self.storage_dir / f"{dom_name}.qcow2"
        if disk_path.exists():
            disk_path.unlink(missing_ok=True)
        oemdrv_disk = self.storage_dir / f"{dom_name}_oemdrv.img"
        if oemdrv_disk.exists():
            oemdrv_disk.unlink(missing_ok=True)
        cidata_disk = self.storage_dir / f"{dom_name}_cidata.img"
        if cidata_disk.exists():
            cidata_disk.unlink(missing_ok=True)
        return True

    def close(self) -> None:
        if self.conn:
            with contextlib.suppress(libvirt.libvirtError):
                self.conn.close()
            self.conn = None
