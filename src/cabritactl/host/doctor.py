"""Read-only checks with separate failed, unknown, warning, and successful results."""

import importlib
import json
import os
import shlex
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from cabritactl.core.executables import find_executable
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.host.setup import active_firewall, probe


@dataclass
class Check:
    name: str
    status: Literal["pass", "fail", "unknown", "warning"]
    detail: str
    remedy: str = ""


def report(checks: list[Check]) -> dict:
    problems = [f"{c.name}: {c.detail}" for c in checks if c.status == "fail"]
    return {
        "ok": not problems,
        "problems": problems,
        "checks": [asdict(c) for c in checks],
    }


def collect(cluster: ResolvedCluster) -> list[Check]:
    m = cluster.manifest
    checks = []
    required = ["ssh"]
    if m.configuration.profile != "none":
        required += ["ansible-playbook", "ansible-galaxy"]
    if m.provider == "libvirt":
        required += ["qemu-img", "virsh", "xorriso", "ip"]
    if m.bootstrap.method == "oemdrv":
        required += ["mkfs.vfat", "mcopy"]
    for tool in required:
        checks.append(
            Check(
                f"tool.{tool}",
                "pass" if find_executable(tool) else "fail",
                find_executable(tool) or "Executable missing",
                "cabritactl host setup --full --apply",
            )
        )
    for name in ("public_key", "private_key"):
        path = getattr(m.access, name)
        checks.append(
            Check(
                f"ssh.{name}",
                "pass" if path.is_file() and os.access(path, os.R_OK) else "fail",
                str(path),
                "Create an SSH key and set access.public_key/private_key",
            )
        )
    if shutil.which("ssh") and m.nodes:
        config = probe(["ssh", "-G", m.nodes[0].ip])
        checks.append(
            Check(
                "ssh.config",
                "pass" if config.returncode == 0 else "fail",
                "SSH configuration parses"
                if config.returncode == 0
                else config.stderr.strip(),
                "Inspect ownership and permissions of the SSH configuration file named in the error",
            )
        )
    if m.configuration.profile != "none" and find_executable("ansible-galaxy"):
        result = probe(
            [
                "ansible-galaxy",
                "collection",
                "list",
                "ansible.posix",
                "--format",
                "json",
            ]
        )
        available = result.returncode == 0 and "ansible.posix" in result.stdout
        checks.append(
            Check(
                "ansible.posix",
                "pass" if available else "fail",
                "Collection required for guest firewall/NFS",
                "ansible-galaxy collection install ansible.posix:2.2.2",
            )
        )
    if m.provider != "libvirt":
        checks.append(
            Check(
                "provider",
                "unknown",
                "Hardware/BMC reachability is checked during provider planning",
            )
        )
        return checks
    checks.append(
        Check(
            "kvm",
            "pass" if Path("/dev/kvm").exists() else "fail",
            "/dev/kvm must be available to system QEMU",
            "Enable hardware virtualization or nested KVM on the host",
        )
    )
    try:
        libvirt = importlib.import_module("libvirt")
        from cabritactl.config import ClusterSettings

        uri = ClusterSettings().libvirt_uri
        if uri != "qemu:///system":
            checks.append(
                Check(
                    "libvirt.uri",
                    "fail",
                    "Managed host deployment requires local qemu:///system",
                )
            )
            return checks
        conn = libvirt.open(uri)
        if conn is None:
            raise RuntimeError("Connection unavailable")
    except Exception as exc:  # noqa: BLE001 — optional provider and authorization failures are diagnostics
        checks.append(
            Check(
                "libvirt.access",
                "fail",
                str(exc),
                "Install the libvirt extra; run host setup --apply and renew your login",
            )
        )
        return checks
    try:
        checks.append(
            Check(
                "libvirt.access",
                "pass",
                "System connection established; doctor performs inspection calls only",
            )
        )
        from cabritactl.providers.libvirt_backend.network import check_network

        try:
            check_network(conn, m)
            checks.append(
                Check(
                    "network.layout",
                    "pass",
                    "Subnet, ownership, bridge and DHCP layout checked",
                )
            )
        except (ValueError, OSError, RuntimeError) as exc:
            checks.append(Check("network.layout", "fail", str(exc)))
        domains = conn.listAllDomains()
        foreign_ips = set()
        foreign_macs = set()
        names = {cluster.resource_name(n) for n in m.nodes}
        for domain in domains:
            if domain.name() not in names:
                root = ET.fromstring(domain.XMLDesc(0))
                foreign_macs.update(
                    e.get("address", "").lower()
                    for e in root.findall("./devices/interface/mac")
                )
        for network in conn.listAllNetworks():
            if network.name() != m.network.network_name:
                root = ET.fromstring(network.XMLDesc(0))
                foreign_ips.update(e.get("ip") for e in root.findall("./ip/dhcp/host"))
        for node in m.nodes:
            if (node.mac or "").lower() in foreign_macs or node.ip in foreign_ips:
                checks.append(
                    Check(
                        "network.address-conflict",
                        "fail",
                        f"{node.hostname} IP/MAC is already declared by another resource",
                    )
                )
        pools = {p.name(): p for p in conn.listAllStoragePools()}
        pool = pools.get(m.libvirt.storage_pool)
        if pool is None or not pool.isActive():
            checks.append(
                Check(
                    "storage.pool",
                    "fail",
                    f"{m.libvirt.storage_pool} is missing/inactive",
                    "cabritactl host setup --apply",
                )
            )
        else:
            root = ET.fromstring(pool.XMLDesc(0))
            path = Path(root.findtext("target/path", "/"))
            unsafe = (
                root.get("type") != "dir"
                or any(part.startswith(".") for part in path.parts)
                or str(path).startswith("/home/")
            )
            checks.append(
                Check(
                    "storage.layout",
                    "fail" if unsafe else "pass",
                    str(path),
                    "Use a directory pool beneath /var/lib/libvirt/images",
                )
            )
            required_bytes = sum(
                n.vm.disk.size_gb * 1024**3
                for n in m.nodes
                if n.vm and cluster.resource_name(n) not in {d.name() for d in domains}
            )
            free = pool.info()[3]
            checks.append(
                Check(
                    "storage.capacity",
                    "pass" if free >= required_bytes else "fail",
                    f"{free} bytes available; {required_bytes} bytes virtual capacity requested",
                )
            )
            checks.append(
                Check(
                    "storage.confinement",
                    "unknown",
                    "Read-only inspection cannot prove QEMU access; libvirt applies per-domain permissions and security labels at start",
                    "Inspect libvirt/QEMU logs and AVC/AppArmor denials if startup fails",
                )
            )
        needed = sum(
            n.vm.memory_mb
            for n in m.nodes
            if n.vm
            and cluster.resource_name(n)
            not in {d.name() for d in domains if d.isActive()}
        )
        available = next(
            (
                int(line.split()[1]) // 1024
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            ),
            0,
        )
        checks.append(
            Check(
                "memory",
                "pass" if available >= needed else "fail",
                f"{available} MiB available; {needed} MiB required to start stopped/new nodes",
            )
        )
        from cabritactl.providers.libvirt_backend.firmware import select_firmware

        for node in m.nodes:
            if node.vm and node.vm.firmware == "efi":
                firmware = select_firmware(
                    conn.getDomainCapabilities(
                        None, None, node.vm.machine_type, "kvm", 0
                    )
                )
                checks.append(Check("firmware", "pass", firmware["firmware_loader"]))
    except Exception as exc:  # noqa: BLE001 — surface inaccessible diagnostic evidence
        checks.append(Check("libvirt.inspect", "fail", str(exc)))
    finally:
        conn.close()
    enforcing = probe(["getenforce"])
    apparmor = probe(["aa-status", "--enabled"])
    if enforcing.returncode == 0:
        checks.append(
            Check(
                "selinux",
                "pass" if enforcing.stdout.strip() == "Enforcing" else "warning",
                enforcing.stdout.strip(),
                "Keep SELinux enforcing; use standard libvirt labeling",
            )
        )
    elif Path("/sys/module/apparmor").exists():
        checks.append(
            Check(
                "apparmor",
                "pass" if apparmor.returncode == 0 else "unknown",
                "Enabled"
                if apparmor.returncode == 0
                else "Cannot verify AppArmor status",
            )
        )
    else:
        checks.append(
            Check("confinement", "warning", "Neither SELinux nor AppArmor was detected")
        )
    try:
        manager = active_firewall()
        checks.append(
            Check(
                "firewall.manager",
                "unknown"
                if manager == "unknown"
                else "warning"
                if manager == "custom"
                else "pass",
                manager,
                "Review host policy; do not disable the firewall",
            )
        )
        if manager == "ufw" or (
            manager == "firewalld" and m.bootstrap.method == "golden-restore"
        ):
            from cabritactl.host.firewall import rules_for

            for rule in rules_for(cluster, manager):
                # Doctor never elevates; unavailable policy is explicitly unknown.
                result = probe(rule.query)
                if result.returncode and rule.add[0] == "ufw":
                    checks.append(
                        Check(
                            "firewall.rules",
                            "unknown",
                            "Cannot read UFW rules without authorization",
                            f"cabritactl host setup --cluster {cluster.source} --apply",
                        )
                    )
                    break
                if rule.add[0] == "ufw" and result.returncode == 0:
                    lines = [
                        shlex.split(line)
                        for line in result.stdout.splitlines()
                        if line.startswith("ufw ")
                    ]
                    if rule.add not in lines:
                        checks.append(
                            Check(
                                "firewall.rules",
                                "fail",
                                "Required UFW rule missing: " + shlex.join(rule.add),
                                f"cabritactl host setup --cluster {cluster.source} --apply",
                            )
                        )
                if rule.add[0] == "firewall-cmd" and result.returncode == 1:
                    checks.append(
                        Check(
                            "firewall.recovery",
                            "fail",
                            "Recovery HTTP rule missing",
                            f"cabritactl host setup --cluster {cluster.source} --apply",
                        )
                    )
        if manager == "firewalld":
            zone = probe(
                ["firewall-cmd", f"--get-zone-of-interface={m.network.bridge}"]
            )
            if zone.returncode == 0 and zone.stdout.strip() not in (
                "libvirt",
                "no zone",
            ):
                checks.append(
                    Check(
                        "firewall.zone",
                        "fail",
                        f"Bridge belongs to {zone.stdout.strip()}, expected libvirt",
                    )
                )
    except (ValueError, RuntimeError) as exc:
        checks.append(
            Check(
                "firewall.manager",
                "unknown",
                str(exc),
                f"cabritactl host setup --cluster {cluster.source} --apply",
            )
        )
    checks.append(
        Check(
            "network.runtime",
            "unknown",
            "Guest DNS/HTTPS/NFS/MPI checks require running nodes; use verify --network after up",
        )
    )
    return checks


def runtime_checks(cluster: ResolvedCluster) -> list[Check]:
    """Check from each guest rather than treating controller internet access as proof."""
    checks = []
    for node in cluster.nodes():
        commands = {
            "address": "ip -j -4 address",
            "gateway": f"ip route get {cluster.manifest.network.gateway}",
            "dns": "getent ahostsv4 dl.rockylinux.org",
            "https": "curl --fail --silent --show-error --max-time 20 -o /dev/null https://dl.rockylinux.org/",
        }
        if cluster.hpc is not None:
            for peer in cluster.nodes():
                if peer.id != node.id:
                    commands[f"peer-ssh.{peer.hostname}"] = (
                        "ssh -o BatchMode=yes -o ConnectTimeout=5 "
                        + shlex.quote(peer.hostname)
                        + " hostname"
                    )
            commands["nfs"] = "test -r /shared/hpl/hosts"
            if node.hostname != cluster.hpc.nfs_server:
                commands["nfs"] = (
                    'test -r /shared/hpl/hosts && test "$(findmnt -n -o FSTYPE --target /shared)" = nfs4'
                )
            else:
                hpc = cluster.hpc
                commands["mpi"] = shlex.join(
                    [
                        hpc.mpi_launcher,
                        "-np",
                        str(len(cluster.nodes()) * hpc.ranks_per_node),
                        "--hostfile",
                        hpc.nfs_mount_dir + "/hpl/hosts",
                        *(
                            ["--mca", "pml", "ob1", "--mca", "btl", "self,tcp"]
                            if hpc.transport == "tcp"
                            else []
                        ),
                        *hpc.mpi_args,
                        "hostname",
                    ]
                )
        for stage, command in commands.items():
            ssh = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=5",
                "-i",
                str(cluster.manifest.access.private_key),
                f"{cluster.manifest.defaults.os.username}@{node.ip}",
                command,
            ]
            result = probe(ssh)
            valid = result.returncode == 0
            if valid and stage == "address":
                addresses = json.loads(result.stdout)
                valid = any(
                    a.get("local") == node.ip
                    for interface in addresses
                    for a in interface.get("addr_info", [])
                )
            if valid and stage == "mpi":
                valid = set(result.stdout.splitlines()) == {
                    n.hostname for n in cluster.nodes()
                }
            if valid and stage.startswith("peer-ssh."):
                valid = result.stdout.strip() == stage.removeprefix("peer-ssh.")
            checks.append(
                Check(
                    f"{node.hostname}.{stage}",
                    "pass" if valid else "fail",
                    (result.stdout or result.stderr).strip(),
                )
            )
    return checks
