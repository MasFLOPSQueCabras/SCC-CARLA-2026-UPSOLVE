import time
from collections.abc import Callable

from rich.console import Console
from rich.table import Table

from scc_carla.config import ClusterSettings
from scc_carla.db import LockError, NodeLifecycle, update_node_state
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock, wait_for_power_state
from scc_carla.providers.base import NodeProvider, PowerState
from scc_carla.providers.factory import get_provider

console = Console()


def _wait_for_node_power(
    prov: NodeProvider,
    node: int,
    hostname: str,
    target_state: PowerState,
    timeout_sec: int,
    initial_delay_sec: float = 0.0,
) -> None:
    if initial_delay_sec > 0:
        time.sleep(initial_delay_sec)

    with console.status(
        f"[cyan]Waiting for {hostname} power state -> {target_state.value}...[/cyan]"
    ):
        reached = wait_for_power_state(
            lambda: prov.get_power_status(node),
            target_state,
            timeout_sec=timeout_sec,
        )

    if reached:
        console.print(
            f"[bold green]✓ {hostname} is confirmed {target_state.value}.[/bold green]"
        )
    else:
        console.print(
            f"[bold yellow]Timed out waiting for {hostname} to reach {target_state.value}.[/bold yellow]"
        )


def _execute_single_power_action(
    prov: NodeProvider,
    settings: ClusterSettings,
    node: int,
    action_label: str,
    action_fn: Callable[[int], bool],
    expected_state: PowerState | None = None,
    wait: bool = False,
    wait_timeout: int = 60,
    initial_delay_sec: float = 0.0,
    new_lifecycle: NodeLifecycle | None = None,
) -> bool:
    hostname = settings.get_hostname(node)
    with console.status(f"[cyan]{action_label.capitalize()} {hostname}...[/cyan]"):
        success = action_fn(node)

    if not success:
        console.print(f"[bold red]Failed to {action_label} {hostname}.[/bold red]")
        return False

    console.print(f"[green]✓[/green] {hostname} {action_label} signal sent.")
    if new_lifecycle is not None:
        update_node_state(settings, node, new_lifecycle)

    if wait and expected_state is not None:
        _wait_for_node_power(
            prov=prov,
            node=node,
            hostname=hostname,
            target_state=expected_state,
            timeout_sec=wait_timeout,
            initial_delay_sec=initial_delay_sec,
        )
    return True


def _run_cluster_power_action(
    settings: ClusterSettings,
    node: list[int] | int | None,
    operation: str,
    action_label: str,
    action_fn: Callable[[NodeProvider, int], bool],
    expected_state: PowerState | None = None,
    wait: bool = False,
    wait_timeout: int = 60,
    force_lock: bool = False,
    provider: str | None = None,
    initial_delay_sec: float = 0.0,
    new_lifecycle: NodeLifecycle | None = None,
) -> None:
    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    try:
        with (
            cluster_lock(
                settings, targets=targets, operation=operation, force=force_lock
            ),
            get_provider(settings, provider) as prov,
        ):
            for n in targets:
                _execute_single_power_action(
                    prov=prov,
                    settings=settings,
                    node=n,
                    action_label=action_label,
                    action_fn=lambda nid: action_fn(prov, nid),
                    expected_state=expected_state,
                    wait=wait,
                    wait_timeout=wait_timeout,
                    initial_delay_sec=initial_delay_sec,
                    new_lifecycle=new_lifecycle,
                )
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )


def power_on_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    wait: bool = False,
    wait_timeout: int = 60,
    force_lock: bool = False,
    provider: str | None = None,
) -> None:
    _run_cluster_power_action(
        settings=settings,
        node=node,
        operation="power-on",
        action_label="power on",
        action_fn=lambda prov, n: prov.power_on(n),
        expected_state=PowerState.ON,
        wait=wait,
        wait_timeout=wait_timeout,
        force_lock=force_lock,
        provider=provider,
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
    mode_str = "graceful" if graceful else "forced"
    _run_cluster_power_action(
        settings=settings,
        node=node,
        operation="power-off",
        action_label=f"power off ({mode_str})",
        action_fn=lambda prov, n: prov.power_off(n, graceful=graceful),
        expected_state=PowerState.OFF,
        wait=wait,
        wait_timeout=wait_timeout,
        force_lock=force_lock,
        provider=provider,
        new_lifecycle=NodeLifecycle.OFFLINE,
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
    mode_str = "graceful" if graceful else "forced"
    _run_cluster_power_action(
        settings=settings,
        node=node,
        operation="power-restart",
        action_label=f"restart ({mode_str})",
        action_fn=lambda prov, n: prov.power_reset(n, graceful=graceful),
        expected_state=PowerState.ON,
        wait=wait,
        wait_timeout=wait_timeout,
        force_lock=force_lock,
        provider=provider,
        initial_delay_sec=3.0,
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

        table.add_row(str(n), hostname, pwr_style, curr_str, avg_str, min_str, max_str)

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
        metrics_sample = prov.get_power_metrics(targets[0])
        if metrics_sample is None:
            return

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
