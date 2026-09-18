from rich.console import Console
from rich.table import Table

from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import ClusterLock, LockError, NodeLifecycle, update_node_state
from scc_carla.nodes import resolve_target_nodes

console = Console()


def power_on_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    force_lock: bool = False,
) -> None:
    try:
        targets = resolve_target_nodes(node, all_nodes)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    if not targets:
        console.print("[bold yellow]No nodes specified. Use -n or -a.[/bold yellow]")
        return

    resources = [f"node-{n}" for n in targets]
    try:
        with (
            ClusterLock(
                settings, resources=resources, operation="power-on", force=force_lock
            ),
            BMCController(settings) as bmc,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(f"[cyan]Powering on {hostname}...[/cyan]"):
                    success = bmc.power_on(n)
                if success:
                    console.print(f"[green]✓[/green] Power on signal sent to {hostname}.")
                else:
                    console.print(f"[bold red]Failed to power on {hostname}.[/bold red]")
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_off_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    graceful: bool = True,
    force_lock: bool = False,
) -> None:
    try:
        targets = resolve_target_nodes(node, all_nodes)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    if not targets:
        console.print("[bold yellow]No nodes specified. Use -n or -a.[/bold yellow]")
        return

    resources = [f"node-{n}" for n in targets]
    mode_str = "graceful" if graceful else "forced"
    try:
        with (
            ClusterLock(
                settings, resources=resources, operation="power-off", force=force_lock
            ),
            BMCController(settings) as bmc,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(f"[cyan]Powering off {hostname} ({mode_str})...[/cyan]"):
                    success = bmc.power_off(n, graceful=graceful)
                if success:
                    update_node_state(settings, n, NodeLifecycle.OFFLINE)
                    console.print(
                        f"[green]✓[/green] {hostname} powered off ({mode_str}) and marked OFFLINE."
                    )
                else:
                    console.print(f"[bold red]Failed to power off {hostname}.[/bold red]")
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_restart_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    graceful: bool = True,
    force_lock: bool = False,
) -> None:
    try:
        targets = resolve_target_nodes(node, all_nodes)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    if not targets:
        console.print("[bold yellow]No nodes specified. Use -n or -a.[/bold yellow]")
        return

    resources = [f"node-{n}" for n in targets]
    mode_str = "graceful" if graceful else "forced"
    try:
        with (
            ClusterLock(
                settings, resources=resources, operation="power-restart", force=force_lock
            ),
            BMCController(settings) as bmc,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(f"[cyan]Restarting {hostname} ({mode_str})...[/cyan]"):
                    success = bmc.reset(n, graceful=graceful)
                if success:
                    console.print(f"[green]✓[/green] {hostname} restart signal sent ({mode_str}).")
                else:
                    console.print(f"[bold red]Failed to restart {hostname}.[/bold red]")
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_status_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
) -> None:
    if node is None and not all_nodes:
        targets = [1, 2, 3]
    else:
        try:
            targets = resolve_target_nodes(node, all_nodes)
        except ValueError as e:
            console.print(f"[bold red]{e}[/bold red]")
            return

    table = Table(
        title="Node Power Status",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Node", justify="center", style="bold")
    table.add_column("Hostname", justify="center")
    table.add_column("BMC IP", justify="center")
    table.add_column("Power State", justify="center")

    with (
        console.status("[cyan]Querying BMC power states...[/cyan]", spinner="dots"),
        BMCController(settings) as bmc,
    ):
        for n in targets:
            hostname = settings.get_hostname(n)
            bmc_ip = settings.get_bmc_ip(n)
            pwr = bmc.get_power_status(n)

            pwr_style = (
                "[bold green]ON[/bold green]"
                if pwr == "ON"
                else (
                    "[dim]OFF[/dim]"
                    if pwr == "OFF"
                    else "[dim]UNKNOWN[/dim]"
                )
            )
            table.add_row(str(n), hostname, bmc_ip, pwr_style)

    console.print()
    console.print(table)
    console.print()
