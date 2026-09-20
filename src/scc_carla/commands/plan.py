"""Terraform-like cluster execution plan and diff command.

Compares declared cluster manifest configuration (values.yaml / configs/clusters/*.yaml)
against observed live state across hypervisor/BMC, database, and operational locks.
"""

from __future__ import annotations

import contextlib
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from scc_core.manifest import ClusterManifest, NodeSpec, load_manifest

from scc_carla.config import ClusterSettings, get_settings
from scc_carla.db import NodeLifecycle, get_active_locks, get_all_nodes
from scc_carla.providers.base import NodeProvider
from scc_carla.providers.factory import get_provider

console = Console()


class ActionType(StrEnum):
    CREATE = "[bold green]+ CREATE[/bold green]"
    UPDATE = "[bold yellow]~ UPDATE[/bold yellow]"
    DESTROY = "[bold red]- DESTROY[/bold red]"
    NOOP = "[dim]= NOOP[/dim]"
    TASK = "[bold cyan]* TASK[/bold cyan]"


def _locate_cluster_manifest(
    cluster_option: Path | None = None,
) -> tuple[Path, ClusterManifest]:
    """Finds and parses the target cluster manifest."""
    candidates = [
        cluster_option,
        Path.cwd() / "values.yaml",
        Path(__file__).parents[3] / "values.yaml",
        Path(__file__).parents[3] / "configs" / "clusters" / "vm-standard.yaml",
        Path(__file__).parents[3] / "configs" / "clusters" / "helvetios-hpc.yaml",
    ]

    for p in candidates:
        if p and p.expanduser().resolve().exists():
            resolved = p.expanduser().resolve()
            return resolved, load_manifest(resolved)

    raise FileNotFoundError(
        "No cluster manifest or values.yaml found. Run 'scc init' or specify '--cluster <path>'."
    )


def plan_command(
    cluster_path: Path | None = None,
    settings: ClusterSettings | None = None,
) -> None:
    """Computes and renders a declarative diff between declared and live cluster state."""
    active_settings = settings or get_settings()

    try:
        manifest_file, manifest = _locate_cluster_manifest(cluster_path)
    except Exception as e:
        console.print(f"[bold red]Failed to load cluster manifest: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    console.print(
        Panel.fit(
            f"[bold cyan]Cluster Manifest:[/bold cyan] [bold]{manifest.name}[/bold] "
            f"([dim]{manifest_file}[/dim])\n"
            f"[bold cyan]Provider:[/bold cyan] {manifest.provider} | "
            f"[bold cyan]Nodes:[/bold cyan] {len(manifest.nodes)} | "
            f"[bold cyan]Description:[/bold cyan] {manifest.description or 'N/A'}",
            title="[bold green]SCC Cluster Execution Plan[/bold green]",
            border_style="green",
        )
    )

    diff_table = Table(
        title="Resource State & Drift Comparison",
        show_header=True,
        header_style="bold magenta",
    )
    diff_table.add_column("Resource", style="bold", min_width=20)
    diff_table.add_column("Type", style="dim", min_width=12)
    diff_table.add_column("Declared State", min_width=28)
    diff_table.add_column("Observed Live State", min_width=24)
    diff_table.add_column("Action", justify="center", min_width=12)

    counts = {
        ActionType.CREATE: 0,
        ActionType.UPDATE: 0,
        ActionType.DESTROY: 0,
        ActionType.NOOP: 0,
        ActionType.TASK: 0,
    }

    # 1. Operational Locks
    active_locks = get_active_locks(active_settings)
    if active_locks:
        lock_holders = ", ".join(f"{l.holder} ({l.operation})" for l in active_locks)
        diff_table.add_row(
            "Cluster Locks",
            "Lock",
            "Target: Free / Unlocked",
            f"[bold yellow]Held by {lock_holders}[/bold yellow]",
            ActionType.UPDATE,
        )
        counts[ActionType.UPDATE] += 1
    else:
        diff_table.add_row(
            "Cluster Locks",
            "Lock",
            "Exclusive cluster lease",
            "[green]Free (No active locks)[/green]",
            ActionType.TASK,
        )
        counts[ActionType.TASK] += 1

    # 2. Network Infrastructure
    net = manifest.network
    diff_table.add_row(
        f"Network Subnet ({net.subnet})",
        "Network",
        f"Gateway: {net.gateway}, DNS: {net.dns}",
        f"Bridge/Subnet {net.subnet}",
        ActionType.NOOP,
    )
    counts[ActionType.NOOP] += 1

    # 3. Provider & Node Inspection
    db_nodes = {n.node_id: n for n in get_all_nodes(active_settings)}

    stack = contextlib.ExitStack()
    prov: NodeProvider | None = None
    try:
        p = get_provider(active_settings, manifest.provider)
        prov = stack.enter_context(p)
    except Exception as exc:  # noqa: BLE001
        diff_table.add_row(
            f"Provider '{manifest.provider}'",
            "Infrastructure",
            "Target hypervisor / BMC connection",
            f"[yellow]Offline / Unreachable ({type(exc).__name__})[/yellow]",
            ActionType.TASK,
        )
        counts[ActionType.TASK] += 1

    with stack:
        for node in manifest.nodes:
            node_id = node.id
            db_node = db_nodes.get(node_id)
            current_lifecycle = (
                db_node.state if db_node else NodeLifecycle.UNPROVISIONED
            )

            if manifest.provider in ("libvirt", "vm"):
                _plan_libvirt_node(
                    prov=prov,
                    node=node,
                    manifest=manifest,
                    diff_table=diff_table,
                    counts=counts,
                    current_lifecycle=current_lifecycle,
                )
            else:
                _plan_helvetios_node(
                    prov=prov,
                    node=node,
                    manifest=manifest,
                    diff_table=diff_table,
                    counts=counts,
                    current_lifecycle=current_lifecycle,
                )

    # 4. Post-Provisioning Configuration
    diff_table.add_row(
        "Ansible Playbook: site.yaml",
        "Configuration",
        f"Roles: common, infiniband, hpc_tune, nfs, spack, hpl ({len(manifest.nodes)} nodes)",
        "Pending OS installation & SSH availability",
        ActionType.TASK,
    )
    counts[ActionType.TASK] += 1

    console.print(diff_table)
    console.print(
        f"\n[bold]Plan:[/bold] "
        f"[green]{counts[ActionType.CREATE]} to create[/green], "
        f"[yellow]{counts[ActionType.UPDATE]} to update[/yellow], "
        f"[red]{counts[ActionType.DESTROY]} to destroy[/red], "
        f"[cyan]{counts[ActionType.TASK]} operational task(s)[/cyan], "
        f"[dim]{counts[ActionType.NOOP]} unchanged[/dim].\n"
    )


def _plan_libvirt_node(
    prov: NodeProvider | None,
    node: NodeSpec,
    manifest: ClusterManifest,
    diff_table: Table,
    counts: dict[ActionType, int],
    current_lifecycle: NodeLifecycle,
) -> None:
    hostname = node.hostname
    vm_spec = node.vm or manifest.defaults.vm

    vcpus = vm_spec.vcpus
    mem_mb = vm_spec.memory_mb
    firmware = vm_spec.firmware
    cpu_mode = vm_spec.cpu_mode
    bus = vm_spec.disk.bus
    declared_desc = (
        f"{vcpus} vCPU, {mem_mb}MB RAM, {firmware.upper()}, {cpu_mode}, {bus}"
    )

    # Check if domain exists on libvirt
    dom_exists = False
    is_active = False
    if prov is not None:
        conn = getattr(prov, "conn", None)
        if conn is not None:
            try:
                dom = conn.lookupByName(hostname)
                dom_exists = True
                is_active = bool(dom.isActive())
            except Exception:  # noqa: BLE001
                dom_exists = False

    if not dom_exists:
        diff_table.add_row(
            f"VM Domain '{hostname}'",
            "Libvirt Domain",
            declared_desc,
            "[red]Non-existent[/red]",
            ActionType.CREATE,
        )
        counts[ActionType.CREATE] += 1

        diff_table.add_row(
            f"Disk Overlay '{hostname}.qcow2'",
            "CoW Storage",
            f"{vm_spec.disk.size_gb} GB, bus: {bus}",
            "[red]Not created[/red]",
            ActionType.CREATE,
        )
        counts[ActionType.CREATE] += 1
    else:
        status_str = (
            "[green]Active (ON)[/green]" if is_active else "[yellow]Shut off[/yellow]"
        )
        diff_table.add_row(
            f"VM Domain '{hostname}'",
            "Libvirt Domain",
            declared_desc,
            f"Exists ({status_str})",
            ActionType.NOOP,
        )
        counts[ActionType.NOOP] += 1


def _plan_helvetios_node(
    prov: NodeProvider | None,
    node: NodeSpec,
    manifest: ClusterManifest,
    diff_table: Table,
    counts: dict[ActionType, int],
    current_lifecycle: NodeLifecycle,
) -> None:
    hostname = node.hostname
    hw_spec = node.hardware or manifest.defaults.hardware
    bios_target = hw_spec.bios_profile
    bmc_ip = node.bmc.ip if node.bmc else "N/A"

    declared_desc = f"IP: {node.ip}, BMC: {bmc_ip}, BIOS: {bios_target}, Root: {hw_spec.target_disk}"

    power_str = "UNREACHABLE"
    if prov is not None:
        try:
            pwr = prov.get_power_status(node.id)
            power_str = pwr.value if hasattr(pwr, "value") else str(pwr)
        except (
            OSError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
            KeyError,
            AttributeError,
        ):
            power_str = "UNREACHABLE"

    observed_desc = f"Power: {power_str}, DB: {current_lifecycle.value}"

    if current_lifecycle in (NodeLifecycle.UNPROVISIONED, NodeLifecycle.OFFLINE):
        diff_table.add_row(
            f"Bare-Metal '{hostname}'",
            "HPC Node",
            declared_desc,
            observed_desc,
            ActionType.CREATE,
        )
        counts[ActionType.CREATE] += 1
    elif current_lifecycle == NodeLifecycle.READY:
        diff_table.add_row(
            f"Bare-Metal '{hostname}'",
            "HPC Node",
            declared_desc,
            f"[green]{observed_desc}[/green]",
            ActionType.NOOP,
        )
        counts[ActionType.NOOP] += 1
    else:
        diff_table.add_row(
            f"Bare-Metal '{hostname}'",
            "HPC Node",
            declared_desc,
            f"[yellow]{observed_desc}[/yellow]",
            ActionType.UPDATE,
        )
        counts[ActionType.UPDATE] += 1
