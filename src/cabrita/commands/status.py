from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cabrita.config import ClusterSettings
from cabrita.core.parallel import ParallelRunner
from cabrita.db import (
    NodeLifecycle,
    get_active_locks,
    get_all_nodes,
    update_node_state,
)
from cabrita.providers.base import NodeProvider
from cabrita.providers.factory import get_provider
from cabrita.ssh import is_ssh_authenticated

console = Console()


def _probe_node(
    prov: NodeProvider,
    settings: ClusterSettings,
    node_id: int,
    key_path: Path | None,
) -> tuple[int, str, str]:
    """Probes provider power status and SSH reachability for a node.

    Returns: (node_id, power_status, reachability_status)
    """
    pwr = prov.get_power_status(node_id)
    power = pwr.value if hasattr(pwr, "value") else str(pwr)
    reachability = "DOWN"

    if power == "ON":
        node_ip = prov.get_node_ip(node_id)
        if is_ssh_authenticated(
            node_ip,
            settings.node_username,
            bastion_ssh_host=prov.paths.bastion_ssh_host,
            key_path=key_path,
            timeout=3,
        ):
            reachability = "SSH READY"
        else:
            reachability = "NO SSH"

    return node_id, power, reachability


def status_command(
    settings: ClusterSettings,
    probe: bool = True,
    provider: str | None = None,
) -> None:
    nodes = get_all_nodes(settings)
    default_key = Path.home() / ".ssh" / "carla_scc_ed25519"
    key_path = default_key if default_key.exists() else None

    live_data: dict[int, tuple[str, str]] = {}
    if probe:
        with (
            console.status(
                "[bold cyan]Probing live cluster hardware and network state...[/bold cyan]",
                spinner="dots",
            ),
            get_provider(settings, provider) as prov,
        ):
            runner = ParallelRunner[int, tuple[int, str, str]](
                max_workers=len(nodes),
                timeout_sec=10.0,
                thread_name_prefix="cabrita-probe",
            )
            probe_results = (
                runner.items([n.node_id for n in nodes])
                .task(lambda nid: _probe_node(prov, settings, nid, key_path))
                .run()
            )

            for nid, tr in probe_results.items():
                if tr.success and tr.value:
                    _, pwr, reach = tr.value
                    live_data[nid] = (pwr, reach)
                else:
                    live_data[nid] = ("UNKNOWN", "DOWN")

            # Reconcile database state based on live findings
            for node in nodes:
                pwr, reach = live_data.get(node.node_id, ("UNKNOWN", "UNKNOWN"))
                new_state: NodeLifecycle | None = None

                if reach == "SSH READY" and node.state in (
                    NodeLifecycle.UNPROVISIONED,
                    NodeLifecycle.INSTALLING,
                    NodeLifecycle.OFFLINE,
                ):
                    new_state = NodeLifecycle.BOOTSTRAPPED
                elif pwr == "OFF" and node.state in (
                    NodeLifecycle.INSTALLING,
                    NodeLifecycle.READY,
                ):
                    new_state = NodeLifecycle.OFFLINE

                if new_state is not None:
                    update_node_state(settings, node.node_id, new_state)

        # Refresh nodes after reconciliation
        nodes = get_all_nodes(settings)

    table = Table(
        title="SCC@CARLA Cluster Nodes",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Node", justify="center", style="bold")
    table.add_column("Hostname", justify="center")
    table.add_column("OS IP", justify="center")
    table.add_column("BMC/Target IP", justify="center")
    if probe:
        table.add_column("Power", justify="center")
        table.add_column("Reachability", justify="center")
    table.add_column("Lifecycle", justify="center")
    table.add_column("Profile", justify="center")
    table.add_column("Last Updated", justify="center")

    for node in nodes:
        match node.state:
            case NodeLifecycle.READY:
                state_style = "[bold green]READY[/bold green]"
            case NodeLifecycle.INSTALLING:
                state_style = "[bold yellow]INSTALLING[/bold yellow]"
            case NodeLifecycle.BOOTSTRAPPED:
                state_style = "[bold blue]BOOTSTRAPPED[/bold blue]"
            case NodeLifecycle.OFFLINE:
                state_style = "[bold red]OFFLINE[/bold red]"
            case _:
                state_style = "[dim]UNPROVISIONED[/dim]"

        row = [
            str(node.node_id),
            node.hostname,
            node.os_ip,
            node.bmc_ip,
        ]

        if probe:
            pwr, reach = live_data.get(node.node_id, ("UNKNOWN", "UNKNOWN"))
            pwr_style = (
                "[bold green]ON[/bold green]"
                if pwr == "ON"
                else ("[dim]OFF[/dim]" if pwr == "OFF" else "[dim]UNKNOWN[/dim]")
            )
            reach_style = (
                "[bold green]SSH READY[/bold green]"
                if reach == "SSH READY"
                else (
                    "[bold yellow]NO SSH[/bold yellow]"
                    if reach == "NO SSH"
                    else "[dim]DOWN[/dim]"
                )
            )
            row.extend([pwr_style, reach_style])

        row.extend(
            [
                state_style,
                node.bios_profile or "factory_baseline",
                node.last_updated or "-",
            ]
        )
        table.add_row(*row)

    console.print()
    console.print(table)

    # Active operational locks
    locks = get_active_locks(settings)
    if locks:
        lock_table = Table(
            title="Active Operational Locks",
            show_header=True,
            header_style="bold yellow",
        )
        lock_table.add_column("Resource", justify="center", style="bold")
        lock_table.add_column("Holder", justify="center")
        lock_table.add_column("Operation", justify="center")
        lock_table.add_column("Acquired At", justify="center")
        lock_table.add_column("Elapsed", justify="center")
        lock_table.add_column("Status", justify="center")

        for lk in locks:
            elapsed_m = lk.elapsed_sec // 60
            elapsed_s = lk.elapsed_sec % 60
            elapsed_str = f"{elapsed_m}m {elapsed_s}s"
            status_str = (
                "[bold red]EXPIRED[/bold red]"
                if lk.is_expired
                else "[bold yellow]LOCKED[/bold yellow]"
            )
            lock_table.add_row(
                lk.resource,
                lk.holder,
                lk.operation,
                lk.acquired_at,
                elapsed_str,
                status_str,
            )

        console.print()
        console.print(lock_table)

    active_provider = provider or settings.provider
    console.print(
        Panel(
            f"[bold]Cluster Configuration[/bold]\n"
            f"• Active Provider: [cyan]{active_provider}[/cyan]\n"
            f"• Team ID: {settings.team_id}\n"
            f"• Gateway: {settings.gateway_ip}\n"
            f"• Bastion HTTP: {settings.bastion_http_ip}:{settings.bastion_http_port}\n"
            f"• Shared Bastion DB: {settings.bastion_ssh_host}:{settings.bastion_state_db_path}",
            title="Cluster Info",
            border_style="dim",
        )
    )
    console.print()


from typing import Annotated

import typer


def status_cli(
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Perform live hardware & network probing across cluster nodes",
        ),
    ] = True,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-P",
            help="Node provider to inspect (libvirt, bmc, or chameleon)",
        ),
    ] = None,
    cluster: Annotated[
        Path | None,
        typer.Option(
            "--cluster",
            "-c",
            help="Path to cluster manifest or values.yaml override file",
        ),
    ] = None,
) -> None:
    """Inspect current cluster state, node lifecycles, and operational locks."""
    from cabrita.config import get_settings
    from cabrita.core.manifest import load_manifest

    settings = get_settings()

    manifest_file = cluster or (
        Path.cwd() / "values.yaml" if (Path.cwd() / "values.yaml").exists() else None
    )
    if manifest_file and manifest_file.exists():
        manifest = load_manifest(manifest_file)
        if provider is None:
            provider = manifest.provider

    status_command(settings, probe=probe, provider=provider)
