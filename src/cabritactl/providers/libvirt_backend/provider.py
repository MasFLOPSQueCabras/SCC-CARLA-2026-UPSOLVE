import contextlib
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import libvirt

from cabritactl.config import ClusterSettings
from cabritactl.core.manifest.models import ClusterManifest, NodeSpec
from cabritactl.core.providers.base import NodeProvider, PowerState, ProviderPaths
from cabritactl.core.templating import TemplateEngine
from cabritactl.providers.libvirt_backend.cidata import generate_cidata
from cabritactl.providers.libvirt_backend.network import ensure_network, remove_network
from cabritactl.providers.libvirt_backend.overlay import create_cow_overlay
from cabritactl.providers.libvirt_backend.storage import ManagedStorage


class LibvirtProvider(NodeProvider):
    """Local virtualized node provider using libvirt-python and QEMU/KVM."""

    def __init__(
        self,
        manifest: ClusterManifest,
        paths: ProviderPaths | None = None,
        uri: str = "qemu:///system",
        storage_dir: Path | None = None,
        template_engine: TemplateEngine | None = None,
        settings: ClusterSettings | None = None,
    ) -> None:
        self.managed_storage = paths is None and storage_dir is None
        self.manifest = manifest
        self.settings = settings
        if settings is not None:
            self.uri = settings.libvirt_uri
        else:
            self.uri = uri
        self._conn: libvirt.virConnect | None = None
        self._paths = paths or ProviderPaths(
            staging_dir=Path.home() / ".cache" / "cabrita" / "staging",
            iso_cache_dir=Path.home() / ".cache" / "cabrita" / "iso",
            storage_dir=storage_dir or (Path("/var/lib/libvirt/images/cabrita")),
            state_db_path=Path.home() / ".config" / "cabrita" / "state.db",
            gateway_ip=manifest.network.gateway,
            dns_ip=manifest.network.dns,
            remote_serve_dir=None,
            bastion_ssh_host=None,
        )

        if self._paths.storage_dir is None:
            raise ValueError("Libvirt requires a storage directory")
        self.storage_dir = self._paths.storage_dir
        self.template_engine = template_engine or TemplateEngine()

    @property
    def conn(self) -> libvirt.virConnect:
        if self._conn is None:
            try:
                c = libvirt.open(self.uri)
            except libvirt.libvirtError as exc:
                raise RuntimeError(
                    f"Cannot connect to {self.uri}: {exc}. Run cabritactl host setup --apply and verify libvirt authorization in this login session."
                ) from exc
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

    @contextmanager
    def deployment_session(self) -> Generator[None]:
        """Ensures local storage directory permissions for QEMU/KVM process."""
        if self.managed_storage:
            _ = self.storage.pool
            ensure_network(self.conn, self.manifest)
        else:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        yield

    @property
    def storage(self) -> ManagedStorage:
        return ManagedStorage(
            self.conn, self.manifest.libvirt.storage_pool, self.manifest.name
        )

    def _get_domain_name(self, node_id: int) -> str:
        if self._get_node_spec(node_id) is None:
            raise ValueError(f"Undeclared node: {node_id}")
        return f"cabrita-{self.manifest.name}-node{node_id}"

    def _get_domain(self, node_id: int) -> libvirt.virDomain | None:
        dom_name = self._get_domain_name(node_id)
        try:
            return self.conn.lookupByName(dom_name)
        except libvirt.libvirtError as exc:
            if exc.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return None
            raise

    def _get_node_spec(self, node_id: int) -> NodeSpec | None:
        if self.manifest:
            for n in self.manifest.nodes:
                if n.id == node_id:
                    return n
        return None

    def node_defined(self, node_id: int) -> bool:
        return self._get_domain(node_id) is not None

    def node_exists(self, node_id: int) -> bool:
        if self.managed_storage:
            return (
                self._get_domain(node_id) is not None
                or self.storage.lookup(f"{self._get_domain_name(node_id)}.qcow2")
                is not None
            )
        return (
            self._get_domain(node_id) is not None
            or (self.storage_dir / f"{self._get_domain_name(node_id)}.qcow2").exists()
        )

    def get_node_ip(self, node_id: int) -> str:
        spec = self._get_node_spec(node_id)
        if spec is None:
            raise ValueError(f"Undeclared node: {node_id}")
        return spec.ip

    def power_on(self, node_id: int) -> bool:
        if self.manifest.network.managed:
            ensure_network(self.conn, self.manifest)
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
        if image_source is None:
            raise ValueError("Provisioning requires an explicitly prepared artifact")
        spec = self._get_node_spec(node_id)
        if spec is None or spec.vm is None or self.manifest is None:
            raise ValueError("Provisioning requires a resolved manifest node")
        if self.node_exists(node_id):
            can_resume = (
                self.managed_storage
                and kwargs.get("resume_install", False)
                and not self.node_defined(node_id)
            )
            if not kwargs.get("reinstall", False) and not can_resume:
                raise RuntimeError("Existing node requires explicit --reinstall")
            self.teardown_node(node_id)
        te = template_engine or self.template_engine
        stg = staging_dir or self.paths.staging_dir
        stg.mkdir(parents=True, exist_ok=True)
        if not self.managed_storage:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        name = self._get_domain_name(node_id)
        disk = self.storage_dir / f"{name}.qcow2"
        vm = spec.vm
        cloud = kwargs["bootstrap_method"] == "cloud-init"
        context = {
            **self.manifest.template_inputs,
            **self.manifest.bootstrap.inputs,
            "network_prefix": ip_network(self.manifest.network.subnet).prefixlen,
            "hostname": spec.hostname,
            "node_ip": spec.ip,
            "gateway_ip": self.manifest.network.gateway,
            "dns_ip": self.manifest.network.dns,
            "mac_address": spec.mac,
            "node_username": self.manifest.defaults.os.username,
            "pubkey": pubkey,
        }
        cidata: Path | None = None
        if self.managed_storage:
            import json

            if cloud:
                info = json.loads(
                    subprocess.check_output(
                        ["qemu-img", "info", "--output=json", image_source], text=True
                    )
                )
                if (
                    info.get("format") != "qcow2"
                    or info.get("backing-filename")
                    or info.get("data-file")
                    or info.get("format-specific", {}).get("data", {}).get("data-file")
                ):
                    raise ValueError("Imported base image must be self-contained")
            image_source = str(self.storage.import_file(Path(image_source)))
            disk = self.storage.disk(
                f"{name}.qcow2", vm.disk.size_gb, Path(image_source) if cloud else None
            )
        if cloud:
            if not self.managed_storage:
                create_cow_overlay(Path(image_source), disk, size=f"{vm.disk.size_gb}G")
            user_data, metadata, network = (
                stg / filename
                for filename in ("user-data", "meta-data", "network-config")
            )
            for filename, destination, override in (
                ("user-data.j2", user_data, kwargs.get("user_data")),
                ("meta-data.j2", metadata, None),
                ("network-config.j2", network, kwargs.get("network_config")),
            ):
                if override is None:
                    te.render_to_file(filename, context, destination)
                else:
                    destination.write_text(
                        te.render_string(Path(override).read_text(), context)
                    )
            cidata = (
                stg if self.managed_storage else self.storage_dir
            ) / f"{name}_cidata.iso"
            generate_cidata(user_data, metadata, network, cidata)
            if self.managed_storage:
                cidata = self.storage.import_file(cidata, cidata.name)
        elif not self.managed_storage:
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk), f"{vm.disk.size_gb}G"],
                check=True,
                capture_output=True,
                timeout=120,
            )
        auxiliary = kwargs.get("oemdrv_path")
        if auxiliary and self.managed_storage:
            auxiliary = str(
                self.storage.import_file(Path(auxiliary), f"{name}_oemdrv.img")
            )
        network_spec = self.manifest.network
        domain_context = {
            "domain_name": name,
            "memory_mb": vm.memory_mb,
            "vcpus": vm.vcpus,
            "iothreads": vm.iothreads,
            "topology": None,
            "cpu_mode": vm.cpu_mode,
            "machine_type": vm.machine_type,
            "firmware": vm.firmware,
            "disk_controller": vm.disk.controller,
            "controller_queues": vm.disk.queues,
            "disk_cache": vm.disk.cache,
            "disk_io": vm.disk.io,
            "disk_discard": vm.disk.discard,
            "disk_image": str(disk),
            "disk_target": "vda" if vm.disk.bus == "virtio" else "sda",
            "disk_bus": vm.disk.bus,
            "cidata_iso": str(cidata) if cidata else None,
            "install_iso": None if cloud else image_source,
            "oemdrv_iso": auxiliary,
            "boot_dev": "hd" if cloud else "cdrom",
            "network_bridge": network_spec.bridge
            if not network_spec.managed and network_spec.bridge != "virbr0"
            else None,
            "network_name": network_spec.network_name,
            "mac_address": spec.mac,
            "nic_model": network_spec.model,
            "vhost": network_spec.vhost,
            "nic_queues": network_spec.queues,
            "graphics": vm.graphics,
        }
        if vm.firmware == "efi":
            from cabritactl.providers.libvirt_backend.firmware import select_firmware

            domain_context.update(
                select_firmware(
                    self.conn.getDomainCapabilities(
                        None, None, vm.machine_type, "kvm", 0
                    )
                )
            )
        xml = te.render("domain.xml.j2", domain_context)
        (stg / "domain.xml").write_text(xml)
        domain = self.conn.defineXML(xml)
        if domain is None:
            raise RuntimeError(f"Failed to define domain {name}")
        domain.create()
        return True

    def post_provision(self, node_id: int) -> None:
        """Remove installation media from the persistent domain and boot its disk."""
        domain = self._get_domain(node_id)
        if domain is None:
            raise RuntimeError(f"Domain disappeared during installation: {node_id}")
        if domain.isActive():
            domain.shutdown()
            deadline = time.monotonic() + 120
            while domain.isActive():
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Guest did not shut down for installation media removal"
                    )
                time.sleep(1)
        xml = ET.fromstring(domain.XMLDesc(0))
        devices = xml.find("devices")
        assert devices is not None
        for disk in devices.findall("disk"):
            target = disk.find("target")
            if disk.get("device") == "cdrom" or (
                target is not None and target.get("bus") == "usb"
            ):
                devices.remove(disk)
        os_element = xml.find("os")
        assert os_element is not None
        for boot in os_element.findall("boot"):
            os_element.remove(boot)
        ET.SubElement(os_element, "boot", {"dev": "hd"})
        self.conn.defineXML(ET.tostring(xml, encoding="unicode"))
        domain.create()

    def teardown_node(self, node_id: int) -> bool:
        dom = self._get_domain(node_id)
        if dom is not None:
            if dom.isActive():
                dom.destroy()
            dom.undefineFlags(
                libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
                | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
                | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
            )
        name = self._get_domain_name(node_id)
        for suffix in (".qcow2", "_cidata.iso", "_oemdrv.img"):
            if self.managed_storage:
                self.storage.remove(f"{name}{suffix}")
            else:
                (self.storage_dir / f"{name}{suffix}").unlink(missing_ok=True)
        return True

    @contextmanager
    def capture_source(self, node_id: int, name: str):
        from tempfile import TemporaryDirectory

        if not self.managed_storage:
            yield self.storage_dir / f"{name}.qcow2"
            return
        with TemporaryDirectory(prefix="cabrita-capture-") as directory:
            yield self.storage.export_chain(f"{name}.qcow2", Path(directory))

    def cleanup_network(self) -> None:
        remove_network(self.conn, self.manifest)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(libvirt.libvirtError):
                self._conn.close()
            self._conn = None
