import contextlib
import importlib.resources as ir
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import libvirt

from cabrita.core.manifest.models import ClusterManifest, NodeSpec, VMSpec
from cabrita.core.oemdrv import generate_oemdrv
from cabrita.core.providers.base import NodeProvider, PowerState, ProviderPaths
from cabrita.core.templating import TemplateEngine
from cabrita.providers.libvirt_backend.cidata import generate_cidata
from cabrita.providers.libvirt_backend.overlay import create_cow_overlay, is_qcow2_image


class LibvirtProvider(NodeProvider):
    """Local virtualized node provider using libvirt-python and QEMU/KVM."""

    def __init__(
        self,
        manifest: ClusterManifest | None = None,
        paths: ProviderPaths | None = None,
        uri: str = "qemu:///system",
        storage_dir: Path | None = None,
        template_engine: TemplateEngine | None = None,
        settings: Any | None = None,
    ) -> None:
        self.manifest = manifest
        self.settings = settings
        if settings is not None:
            self.uri = getattr(settings, "libvirt_uri", uri)
        else:
            self.uri = uri
        self._conn: libvirt.virConnect | None = None
        self._paths = paths or ProviderPaths(
            staging_dir=Path.home() / ".cache" / "cabrita" / "staging",
            iso_cache_dir=Path.home() / ".cache" / "cabrita" / "iso",
            storage_dir=storage_dir
            or (Path.home() / ".local" / "share" / "cabrita" / "libvirt_storage"),
            state_db_path=Path.home() / ".config" / "cabrita" / "state.db",
            gateway_ip=manifest.network.gateway if manifest else "192.168.122.1",
            dns_ip=manifest.network.dns if manifest else "192.168.122.1",
            remote_serve_dir=None,
            bastion_ssh_host=None,
        )

        resolved_storage = (
            getattr(settings, "libvirt_storage_dir", None)
            if settings
            else self._paths.storage_dir
        )
        self.storage_dir = Path(
            resolved_storage or self._paths.storage_dir or "/var/lib/libvirt/images"
        )
        self.template_engine = template_engine or TemplateEngine()

    @property
    def conn(self) -> libvirt.virConnect:
        if self._conn is None:
            c = libvirt.open(self.uri)
            if not c:
                raise RuntimeError(f"Failed to connect to libvirt URI: {self.uri}")
            libvirt.registerErrorHandler(lambda ctx, err: None, None)
            self._conn = c
        return self._conn

    @property
    def name(self) -> str:
        return "libvirt"

    @property
    def paths(self) -> ProviderPaths:
        return self._paths

    @classmethod
    def list_presets(cls) -> list[str]:
        return ["standard", "hw-optimized"]

    @classmethod
    def get_preset_config(cls, profile: str = "standard") -> str:
        filename = (
            "vm-hw-optimized.yaml"
            if profile.lower() in ("hw-optimized", "cabrita")
            else "vm-standard.yaml"
        )
        ref = ir.files("cabrita.providers.libvirt_backend").joinpath(
            "configs", filename
        )
        return ref.read_text(encoding="utf-8")

    @classmethod
    def get_templates_dir(cls) -> Path | None:
        try:
            ref = ir.files("cabrita.providers.libvirt_backend").joinpath("templates")
            with ir.as_file(ref) as p:
                if p.is_dir():
                    return Path(p)
        except ModuleNotFoundError, TypeError, FileNotFoundError:
            pass
        return None

    @contextmanager
    def deployment_session(self) -> Generator[None]:
        """Ensures local storage directory permissions for QEMU/KVM process."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.storage_dir.chmod(0o777)
        except OSError:
            pass
        yield

    def _get_domain_name(self, node_id: int) -> str:
        prefix = (
            getattr(self.settings, "libvirt_domain_prefix", "cabrita-")
            if self.settings
            else "cabrita-"
        )
        return f"{prefix}node{node_id}"

    def _get_domain(self, node_id: int) -> libvirt.virDomain | None:
        if self.conn is None:
            return None
        dom_name = self._get_domain_name(node_id)
        try:
            return self.conn.lookupByName(dom_name)
        except libvirt.libvirtError:
            return None

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
        if self.settings and hasattr(self.settings, "get_node_ip"):
            return self.settings.get_node_ip(node_id)
        return f"192.168.122.10{node_id}"

    def power_on(self, node_id: int) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            return False
        state, _ = dom.state()
        if state == libvirt.VIR_DOMAIN_SHUTOFF:
            dom.create()
        return True

    def power_off(self, node_id: int, graceful: bool = True) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            return False
        state, _ = dom.state()
        if state != libvirt.VIR_DOMAIN_SHUTOFF:
            if graceful:
                try:
                    dom.shutdown()
                except libvirt.libvirtError:
                    dom.destroy()
            else:
                dom.destroy()
        return True

    def power_reset(self, node_id: int, graceful: bool = True) -> bool:
        dom = self._get_domain(node_id)
        if dom is None:
            return False
        if graceful:
            try:
                dom.reboot(0)
                return True
            except libvirt.libvirtError:
                pass
        dom.reset(0)
        return True

    def get_power_status(self, node_id: int) -> PowerState:
        dom = self._get_domain(node_id)
        if dom is None:
            return PowerState.OFF
        state, _ = dom.state()
        match state:
            case libvirt.VIR_DOMAIN_RUNNING:
                return PowerState.ON
            case libvirt.VIR_DOMAIN_SHUTOFF | libvirt.VIR_DOMAIN_CRASHED:
                return PowerState.OFF
            case _:
                return PowerState.UNKNOWN

    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        return None

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
        te = template_engine or self.template_engine or TemplateEngine()
        stg = staging_dir or self.paths.staging_dir
        stg.mkdir(parents=True, exist_ok=True)

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

        # 1. Resolve source image
        raw_source = image_source or kwargs.get("image_path") or kwargs.get("iso_path")
        chosen_img: str | None = str(raw_source) if raw_source else None

        if chosen_img is None:
            golden_cand = (
                Path.home()
                / ".cache"
                / "cabrita"
                / "golden"
                / "golden-rocky-base.qcow2"
            )
            cloud_name = (
                self.manifest.defaults.os.cloud_image
                if self.manifest
                and self.manifest.defaults
                and self.manifest.defaults.os.cloud_image
                else getattr(
                    self.settings,
                    "cloud_image_name",
                    "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2",
                )
            )
            cloud_cand = self.paths.iso_cache_dir.parent / "images" / cloud_name
            iso_name = (
                self.manifest.defaults.os.iso_name
                if self.manifest
                and self.manifest.defaults
                and self.manifest.defaults.os.iso_name
                else getattr(self.settings, "iso_name", "Rocky-10.2-x86_64-minimal.iso")
            )
            iso_cand = self.paths.iso_cache_dir / iso_name

            if golden_cand.exists():
                chosen_img = str(golden_cand)
            elif cloud_cand.exists():
                chosen_img = str(cloud_cand)
            elif iso_cand.exists():
                chosen_img = str(iso_cand)

        is_boot_iso = bool(chosen_img and not is_qcow2_image(chosen_img))
        overlay_size = f"{vm_spec.disk.size_gb}G"
        oemdrv_iso_str: str | None = None

        if not is_boot_iso and chosen_img is not None:
            # Golden/Cloud QCOW2 Base Image: Instant CoW overlay + Cloud-Init CIDATA
            if progress_callback:
                progress_callback("Creating CoW overlay & Cloud-Init drive...")

            create_cow_overlay(
                base_image=Path(chosen_img),
                overlay_path=disk_path,
                size=overlay_size,
            )

            cidata_target = self.storage_dir / f"{dom_name}_cidata.img"
            user_data_path = stg / f"user_data_{dom_name}"
            meta_data_path = stg / f"meta_data_{dom_name}"
            network_config_path = stg / f"network_config_{dom_name}"

            mac_address = spec.mac if spec else f"52:54:00:72:01:0{node_id}"
            username = (
                self.manifest.defaults.os.username
                if self.manifest and self.manifest.defaults
                else (self.settings.node_username if self.settings else "scct-2672")
            )
            hostname = (
                spec.hostname
                if spec
                else (
                    self.settings.get_hostname(node_id)
                    if self.settings
                    else f"node{node_id}"
                )
            )

            ci_context = {
                "hostname": hostname,
                "node_ip": self.get_node_ip(node_id),
                "gateway_ip": self.paths.gateway_ip,
                "dns_ip": self.paths.dns_ip,
                "mac_address": mac_address,
                "node_username": username,
                "pubkey": pubkey,
            }
            te.render_to_file("user-data.j2", ci_context, user_data_path)
            te.render_to_file("meta-data.j2", ci_context, meta_data_path)
            te.render_to_file("network-config.j2", ci_context, network_config_path)

            generate_cidata(
                user_data_path=user_data_path,
                meta_data_path=meta_data_path,
                network_config_path=network_config_path,
                output_path=cidata_target,
                template_engine=te,
            )
            cidata_iso_path = cidata_target
            install_iso_str = None
        else:
            # Minimal Bootable ISO Installer with Kickstart OEMDRV
            if progress_callback:
                progress_callback(
                    "Allocating blank target drive for ISO kickstart install..."
                )

            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk_path), overlay_size],
                check=True,
                capture_output=True,
            )
            cidata_iso_path = None

            oemdrv_cand = kwargs.get("oemdrv_path")
            if oemdrv_cand:
                oemdrv_iso_str = str(Path(oemdrv_cand).resolve())
            else:
                ks_cfg_path = kwargs.get("ks_cfg_path")
                if not ks_cfg_path:
                    ks_cfg_path = stg / f"ks_node{node_id}.cfg"
                    username = (
                        self.manifest.defaults.os.username
                        if self.manifest and self.manifest.defaults
                        else (
                            self.settings.node_username
                            if self.settings
                            else "scct-2672"
                        )
                    )
                    hostname = (
                        spec.hostname
                        if spec
                        else (
                            self.settings.get_hostname(node_id)
                            if self.settings
                            else f"node{node_id}"
                        )
                    )
                    context = {
                        "node_ip": self.get_node_ip(node_id),
                        "gateway_ip": self.paths.gateway_ip,
                        "dns_ip": self.paths.dns_ip,
                        "hostname": hostname,
                        "node_username": username,
                        "pubkey": pubkey,
                        "target_disk": "vda",
                    }
                    te.render_to_file("kickstart/ks.cfg.j2", context, ks_cfg_path)

                if Path(ks_cfg_path).exists():
                    oemdrv_target = self.storage_dir / f"{dom_name}_oemdrv.img"
                    try:
                        generate_oemdrv(
                            Path(ks_cfg_path),
                            oemdrv_target,
                            template_engine=te,
                        )
                        oemdrv_iso_str = str(oemdrv_target.resolve())
                    except subprocess.CalledProcessError, OSError:
                        oemdrv_iso_str = None

            install_iso_str = str(Path(chosen_img).resolve()) if chosen_img else None

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

        if progress_callback:
            progress_callback("Defining and starting VM domain in QEMU/KVM...")

        domain_xml = te.render("domain.xml.j2", domain_context)
        if self.conn is None:
            return False
        dom = self.conn.defineXML(domain_xml)
        if dom is None:
            return False
        dom.create()
        return True

    def post_provision(self, node_id: int) -> None:
        pass

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
        cidata_target = self.storage_dir / f"{dom_name}_cidata.img"
        oemdrv_target = self.storage_dir / f"{dom_name}_oemdrv.img"

        for p in (disk_path, cidata_target, oemdrv_target):
            if p.exists():
                with contextlib.suppress(OSError):
                    p.unlink()

        return True

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(libvirt.libvirtError):
                self._conn.close()
            self._conn = None
