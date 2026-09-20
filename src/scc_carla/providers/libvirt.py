import contextlib
import subprocess
from pathlib import Path
from typing import Any

import libvirt

from scc_carla.config import ClusterSettings
from scc_carla.providers.base import NodeProvider, PowerState
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
        self.storage_dir = Path.home() / ".cache" / "scc_carla" / "libvirt"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.template_engine = TemplateEngine()

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
        dom = self._get_domain(node_id)
        if dom is None:
            return None

        try:
            info = dom.info()
            # info: [state, maxMem, memory, nrVirtCpu, cpuTime]
            cpu_time_ns = info[4]
            memory_kib = info[2]
            return {
                "PresentPowerWatts": 45.0,  # Simulated baseline power for VM
                "AveragePowerWatts": 42.0,
                "PeakPowerWatts": 65.0,
                "CpuTimeSec": cpu_time_ns / 1_000_000_000.0,
                "MemoryUsageMiB": memory_kib / 1024.0,
            }
        except libvirt.libvirtError:
            return None

    def _generate_domain_xml(
        self,
        dom_name: str,
        disk_path: Path,
        cdrom_path: Path | None = None,
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
            "memory_mib": memory_mib,
            "vcpu": vcpu,
            "network": self.settings.libvirt_network,
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

        # 1. Create disk image if it does not exist
        if not disk_path.exists():
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk_path), "20G"],
                check=True,
                capture_output=True,
            )

        # 2. Check if domain already defined
        dom = self._get_domain(node_id)
        if dom is None:
            if self.conn is None:
                raise RuntimeError("Libvirt connection is closed")
            xml_desc = self._generate_domain_xml(
                dom_name=dom_name,
                disk_path=disk_path,
                cdrom_path=iso_path,
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
        return True

    def close(self) -> None:
        if self.conn:
            with contextlib.suppress(libvirt.libvirtError):
                self.conn.close()
            self.conn = None
