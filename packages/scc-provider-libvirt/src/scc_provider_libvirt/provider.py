import contextlib
import subprocess
from pathlib import Path
from typing import Any

import libvirt
from scc_core.manifest.models import ClusterManifest, NodeSpec, VMSpec
from scc_core.oemdrv import generate_oemdrv
from scc_core.providers.base import NodeProvider, PowerState, ProviderPaths
from scc_core.templating import TemplateEngine

from scc_provider_libvirt.cidata import generate_cidata
from scc_provider_libvirt.overlay import create_cow_overlay, is_qcow2_image


class LibvirtProvider(NodeProvider):
    """Local virtualized node provider using libvirt-python and QEMU/KVM."""

    def __init__(
        self,
        manifest: ClusterManifest | None = None,
        paths: ProviderPaths | None = None,
        uri: str = "qemu:///system",
        storage_dir: Path | None = None,
        template_engine: TemplateEngine | None = None,
    ) -> None:
        self.manifest = manifest
        self.uri = uri
        self.conn = libvirt.open(self.uri)
        if not self.conn:
            raise RuntimeError(f"Failed to connect to libvirt URI: {self.uri}")

        libvirt.registerErrorHandler(lambda ctx, err: None, None)

        self._paths = paths or ProviderPaths(
            staging_dir=Path.home() / ".cache" / "scc_carla" / "staging",
            iso_cache_dir=Path.home() / ".cache" / "scc_carla" / "iso",
            storage_dir=storage_dir
            or (Path.home() / ".local" / "share" / "scc_carla" / "libvirt_storage"),
            state_db_path=Path.home() / ".config" / "scc_carla" / "state.db",
            gateway_ip=manifest.network.gateway if manifest else "192.168.122.1",
            dns_ip=manifest.network.dns if manifest else "192.168.122.1",
        )
        self.storage_dir = self._paths.storage_dir or (
            Path.home() / ".local" / "share" / "scc_carla" / "libvirt_storage"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        package_templates = Path(__file__).parent / "templates"
        self.template_engine = template_engine or TemplateEngine(
            search_paths=[package_templates]
        )

    @property
    def name(self) -> str:
        return "libvirt"

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
        return f"192.168.122.10{node_id}"

    def _get_domain_name(self, node_id: int) -> str:
        spec = self._get_node_spec(node_id)
        if spec:
            return f"scc-{spec.hostname}"
        return f"scc-node{node_id}"

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
            return False
        try:
            state, _ = dom.state()
            if state == libvirt.VIR_DOMAIN_RUNNING:
                return True
            dom.create()
            return True
        except libvirt.libvirtError:
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
        except libvirt.libvirtError:
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
        except libvirt.libvirtError:
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
        return None

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

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.storage_dir.chmod(0o777)
        except OSError:
            pass

        # Cleanup existing domain if present
        if self._get_domain(node_id) is not None:
            self.teardown_node(node_id)

        spec = self._get_node_spec(node_id)
        vm_spec = (
            spec.vm
            if spec and spec.vm
            else (self.manifest.defaults.vm if self.manifest else VMSpec())
        )

        is_boot_iso = bool(iso_path and not is_qcow2_image(iso_path))
        image_path = kwargs.get("image_path")
        cloud_base_image = None

        if not is_boot_iso:
            if image_path and is_qcow2_image(image_path):
                cloud_base_image = Path(image_path)
            elif iso_path and is_qcow2_image(iso_path):
                cloud_base_image = Path(iso_path)
            else:
                golden_cand = (
                    Path.home()
                    / ".cache"
                    / "scc_carla"
                    / "golden"
                    / "golden-rocky-base.qcow2"
                )
                if golden_cand.exists():
                    cloud_base_image = golden_cand

        overlay_size = f"{vm_spec.disk.size_gb}G"
        oemdrv_iso_str: str | None = None

        if cloud_base_image is not None:
            create_cow_overlay(
                base_image=cloud_base_image,
                overlay_path=disk_path,
                size=overlay_size,
            )

            cidata_target = self.storage_dir / f"{dom_name}_cidata.img"
            user_data_path = self.paths.staging_dir / f"user_data_{dom_name}"
            meta_data_path = self.paths.staging_dir / f"meta_data_{dom_name}"
            network_config_path = self.paths.staging_dir / f"network_config_{dom_name}"

            mac_address = spec.mac if spec else f"52:54:00:72:01:0{node_id}"
            ci_context = {
                "hostname": spec.hostname if spec else f"node{node_id}",
                "node_ip": self.get_node_ip(node_id),
                "gateway_ip": self.paths.gateway_ip,
                "dns_ip": self.paths.dns_ip,
                "mac_address": mac_address,
                "node_username": self.manifest.defaults.os.username
                if self.manifest
                else "scct-2672",
                "pubkey": pubkey,
            }
            self.template_engine.render_to_file(
                "user-data.j2", ci_context, user_data_path
            )
            self.template_engine.render_to_file(
                "meta-data.j2", ci_context, meta_data_path
            )
            self.template_engine.render_to_file(
                "network-config.j2", ci_context, network_config_path
            )

            generate_cidata(
                user_data_path=user_data_path,
                meta_data_path=meta_data_path,
                network_config_path=network_config_path,
                output_path=cidata_target,
                template_engine=self.template_engine,
            )
            cidata_iso_path = cidata_target
        else:
            # Fallback blank disk for clean ISO installation
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk_path), overlay_size],
                check=True,
                capture_output=True,
            )
            cidata_iso_path = None

            oemdrv_cand = kwargs.get("oemdrv_path")
            if oemdrv_cand:
                oemdrv_iso_str = str(Path(oemdrv_cand).resolve())
            elif is_boot_iso and ks_cfg_path and Path(ks_cfg_path).exists():
                oemdrv_target = self.storage_dir / f"{dom_name}_oemdrv.img"
                try:
                    generate_oemdrv(
                        Path(ks_cfg_path),
                        oemdrv_target,
                        template_engine=self.template_engine,
                    )
                    oemdrv_iso_str = str(oemdrv_target.resolve())
                except subprocess.CalledProcessError, OSError:
                    oemdrv_iso_str = None

        mac_address = spec.mac if spec else f"52:54:00:72:01:0{node_id}"
        net_bridge = (
            self.manifest.network.bridge
            if self.manifest and self.manifest.network.bridge != "virbr0"
            else None
        )
        if (
            self.manifest
            and hasattr(self.manifest.network, "bridge")
            and self.manifest.network.bridge.startswith("cabrita")
        ):
            net_bridge = self.manifest.network.bridge

        install_iso_str = (
            str(Path(iso_path).resolve()) if is_boot_iso and iso_path else None
        )
        if kwargs.get("oemdrv_path"):
            oemdrv_iso_str = str(Path(kwargs["oemdrv_path"]).resolve())

        domain_context = {
            "domain_name": dom_name,
            "memory_mb": vm_spec.memory_mb,
            "vcpus": vm_spec.vcpus,
            "iothreads": vm_spec.iothreads,
            "cpu_mode": vm_spec.cpu_mode,
            "machine_type": vm_spec.machine_type,
            "firmware": vm_spec.firmware,
            "disk_controller": vm_spec.disk.controller,
            "controller_queues": vm_spec.disk.queues,
            "disk_cache": vm_spec.disk.cache,
            "disk_io": vm_spec.disk.io,
            "disk_discard": vm_spec.disk.discard,
            "disk_image": str(disk_path.resolve()),
            "disk_target": "sda" if vm_spec.disk.bus == "scsi" else "vda",
            "disk_bus": vm_spec.disk.bus,
            "cidata_iso": str(cidata_iso_path.resolve()) if cidata_iso_path else None,
            "install_iso": install_iso_str,
            "oemdrv_iso": oemdrv_iso_str,
            "boot_dev": "cdrom" if is_boot_iso else "hd",
            "network_bridge": net_bridge,
            "network_name": self.manifest.network.network_name
            if self.manifest
            else "default",
            "mac_address": mac_address,
            "nic_model": self.manifest.network.model if self.manifest else "virtio",
            "vhost": self.manifest.network.vhost if self.manifest else True,
            "nic_queues": self.manifest.network.queues if self.manifest else 2,
            "graphics": vm_spec.graphics,
        }

        domain_xml = self.template_engine.render("domain.xml.j2", domain_context)
        if self.conn is None:
            return False
        dom = self.conn.defineXML(domain_xml)
        if dom is None:
            return False
        dom.create()
        return True

    def teardown_node(self, node_id: int) -> bool:
        dom = self._get_domain(node_id)
        if dom is not None:
            with contextlib.suppress(libvirt.libvirtError):
                state, _ = dom.state()
                if state != libvirt.VIR_DOMAIN_SHUTOFF:
                    dom.destroy()
            with contextlib.suppress(libvirt.libvirtError):
                dom.undefineFlags(
                    libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
                    | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
                    | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
                )

        dom_name = self._get_domain_name(node_id)
        disk_path = self.storage_dir / f"{dom_name}.qcow2"
        cidata_path = self.storage_dir / f"{dom_name}_cidata.img"
        for p in (disk_path, cidata_path):
            if p.exists():
                with contextlib.suppress(OSError):
                    p.unlink()
        return True

    def close(self) -> None:
        if self.conn:
            with contextlib.suppress(libvirt.libvirtError):
                self.conn.close()
            self.conn = None
