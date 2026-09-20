import time

from rich.console import Console
from rich.table import Table

from scc_carla.config import ClusterSettings
from scc_carla.db import LockError, NodeLifecycle, update_node_state
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock, wait_for_power_state
from scc_carla.providers.base import NodeProvider, PowerState
from scc_carla.providers.factory import get_provider

console = Console()


def power_on_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    wait: bool = False,
    wait_timeout: int = 60,
    force_lock: bool = False,
    provider: str | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    try:
        with (
            cluster_lock(
                settings, targets=targets, operation="power-on", force=force_lock
            ),
            get_provider(settings, provider) as prov,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(f"[cyan]Powering on {hostname}...[/cyan]"):
                    success = prov.power_on(n)
                if success:
                    console.print(f"[green]✓[/green] Power on signal sent to {hostname}.")
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} power state -> ON...[/cyan]"
                        ):
                            if wait_for_power_state(
                                lambda n=n: prov.get_power_status(n),
                                PowerState.ON,
                                timeout_sec=wait_timeout,
                            ):
                                console.print(
                                    f"[bold green]✓ {hostname} is confirmed ON.[/bold green]"
                                )
                            else:
                                console.print(
                                    f"[bold yellow]Timed out waiting for {hostname} to reach ON.[/bold yellow]"
                                )
                else:
                    console.print(f"[bold red]Failed to power on {hostname}.[/bold red]")
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_off_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    graceful: bool = True,
    wait: bool = False,
    wait_timeout: int = 60,
    force_lock: bool = False,
    provider: str | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    mode_str = "graceful" if graceful else "forced"
    try:
        with (
            cluster_lock(
                settings, targets=targets, operation="power-off", force=force_lock
            ),
            get_provider(settings, provider) as prov,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(
                    f"[cyan]Powering off {hostname} ({mode_str})...[/cyan]"
                ):
                    success = prov.power_off(n, graceful=graceful)
                if success:
                    update_node_state(settings, n, NodeLifecycle.OFFLINE)
                    console.print(
                        f"[green]✓[/green] Power off signal sent to {hostname} ({mode_str})."
                    )
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} power state -> OFF...[/cyan]"
                        ):
                            if wait_for_power_state(
                                lambda n=n: prov.get_power_status(n),
                                PowerState.OFF,
                                timeout_sec=wait_timeout,
                            ):
                                console.print(
                                    f"[bold green]✓ {hostname} is confirmed OFF.[/bold green]"
                                )
                            else:
                                console.print(
                                    f"[bold yellow]Timed out waiting for {hostname} to reach OFF.[/bold yellow]"
                                )
                else:
                    console.print(
                        f"[bold red]Failed to power off {hostname}.[/bold red]"
                    )
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_restart_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    graceful: bool = True,
    wait: bool = False,
    wait_timeout: int = 60,
    force_lock: bool = False,
    provider: str | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    mode_str = "graceful" if graceful else "forced"
    try:
        with (
            cluster_lock(
                settings,
                targets=targets,
                operation="power-restart",
                force=force_lock,
            ),
            get_provider(settings, provider) as prov,
        ):
            for n in targets:
                hostname = settings.get_hostname(n)
                with console.status(
                    f"[cyan]Restarting {hostname} ({mode_str})...[/cyan]"
                ):
                    success = prov.power_reset(n, graceful=graceful)
                if success:
                    console.print(
                        f"[green]✓[/green] {hostname} restart signal sent ({mode_str})."
                    )
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} to cycle and reach ON...[/cyan]"
                        ):
                            time.sleep(3)
                            if wait_for_power_state(
                                lambda n=n: prov.get_power_status(n),
                                PowerState.ON,
                                timeout_sec=wait_timeout,
                            ):
                                console.print(
                                    f"[bold green]✓ {hostname} reboot complete and confirmed ON.[/bold green]"
                                )
                            else:
                                console.print(
                                    f"[bold yellow]Timed out waiting for {hostname} to reach ON.[/bold yellow]"
                                )
                else:
                    console.print(f"[bold red]Failed to restart {hostname}.[/bold red]")
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_status_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    provider: str | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
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
    table.add_column("Target IP", justify="center")
    table.add_column("Power State", justify="center")

    with (
        console.status("[cyan]Querying power states...[/cyan]", spinner="dots"),
        get_provider(settings, provider) as prov,
    ):
        for n in targets:
            hostname = settings.get_hostname(n)
            target_ip = settings.get_node_ip(n)
            pwr = prov.get_power_status(n)

            match pwr:
                case PowerState.ON:
                    pwr_style = "[bold green]ON[/bold green]"
                case PowerState.OFF:
                    pwr_style = "[dim]OFF[/dim]"
                case PowerState.RESTARTING:
                    pwr_style = "[bold yellow]RESTARTING[/bold yellow]"
                case _:
                    pwr_style = "[dim]UNKNOWN[/dim]"

            table.add_row(str(n), hostname, target_ip, pwr_style)

    console.print()
    console.print(table)
    console.print()


def _render_metrics_table(
    prov: NodeProvider, settings: ClusterSettings, targets: list[int]
) -> Table:
    table = Table(
        title="Cluster Power Draw Telemetry",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Node", justify="center", style="bold")
    table.add_column("Hostname", justify="center")
    table.add_column("Power State", justify="center")
    table.add_column("Current Draw", justify="center")
    table.add_column("20-Min Avg", justify="center")
    table.add_column("Min Draw", justify="center")
    table.add_column("Peak Draw", justify="center")

    total_current = 0.0
    total_avg = 0.0
    total_max = 0.0

    for n in targets:
        hostname = settings.get_hostname(n)
        metrics = prov.get_power_metrics(n) or {}
        pwr = metrics.get("power_state") or prov.get_power_status(n).value
        curr = metrics.get("current_watts") or metrics.get("PresentPowerWatts")
        avg = metrics.get("average_watts") or metrics.get("AveragePowerWatts")
        min_w = metrics.get("min_watts")
        max_w = metrics.get("max_watts") or metrics.get("PeakPowerWatts")

        if curr is not None:
            total_current += float(curr)
        if avg is not None:
            total_avg += float(avg)
        if max_w is not None:
            total_max += float(max_w)

        match pwr:
            case "ON" | PowerState.ON:
                pwr_style = "[bold green]ON[/bold green]"
            case "OFF" | PowerState.OFF:
                pwr_style = "[dim]OFF[/dim]"
            case _:
                pwr_style = "[dim]UNKNOWN[/dim]"

        curr_str = (
            f"[bold green]{curr} W[/bold green]" if curr is not None else "[dim]-[/dim]"
        )
        avg_str = f"{avg} W" if avg is not None else "[dim]-[/dim]"
        min_str = f"{min_w} W" if min_w is not None else "[dim]-[/dim]"
        max_str = (
            f"[bold yellow]{max_w} W[/bold yellow]"
            if max_w is not None
            else "[dim]-[/dim]"
        )

        table.add_row(
            str(n), hostname, pwr_style, curr_str, avg_str, min_str, max_str
        )

    if len(targets) > 1:
        total_curr_str = (
            f"[bold green]{total_current:.1f} W ({total_current / 1000:.2f} kW)[/bold green]"
            if total_current > 0
            else "[dim]0 W[/dim]"
        )
        total_avg_str = (
            f"{total_avg:.1f} W ({total_avg / 1000:.2f} kW)"
            if total_avg > 0
            else "[dim]0 W[/dim]"
        )
        total_max_str = (
            f"[bold yellow]{total_max:.1f} W ({total_max / 1000:.2f} kW)[/bold yellow]"
            if total_max > 0
            else "[dim]0 W[/dim]"
        )
        table.add_section()
        table.add_row(
            "[bold]Total[/bold]",
            "[bold]Cluster[/bold]",
            "-",
            total_curr_str,
            total_avg_str,
            "-",
            total_max_str,
        )

    return table


def power_metrics_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    watch: bool = False,
    interval: float = 2.0,
    provider: str | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    with get_provider(settings, provider) as prov:
        if watch:
            from rich.live import Live

            with Live(
                _render_metrics_table(prov, settings, targets),
                console=console,
                refresh_per_second=1,
            ) as live:
                try:
                    while True:
                        time.sleep(interval)
                        live.update(_render_metrics_table(prov, settings, targets))
                except KeyboardInterrupt:
                    pass
        else:
            with console.status(
                "[cyan]Reading power telemetry...[/cyan]",
                spinner="dots",
            ):
                table = _render_metrics_table(prov, settings, targets)
            console.print()
            console.print(table)
            console.print()
