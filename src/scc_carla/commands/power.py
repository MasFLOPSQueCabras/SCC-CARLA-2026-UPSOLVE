import time

from rich.console import Console
from rich.table import Table

from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import ClusterLock, LockError, NodeLifecycle, update_node_state
from scc_carla.nodes import resolve_target_nodes

console = Console()


def _wait_for_power_state(
    bmc: BMCController,
    node_id: int,
    expected_state: str,
    timeout_sec: int = 60,
    poll_interval: float = 2.0,
) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        state = bmc.get_power_status(node_id)
        if state == expected_state:
            return True
        time.sleep(poll_interval)
    return False


def power_on_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    wait: bool = False,
    wait_timeout: int = 60,
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
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} power state -> ON...[/cyan]"
                        ):
                            if _wait_for_power_state(bmc, n, "ON", timeout_sec=wait_timeout):
                                console.print(f"[bold green]✓ {hostname} is confirmed ON.[/bold green]")
                            else:
                                console.print(
                                    f"[bold yellow]Timed out waiting for {hostname} to reach ON.[/bold yellow]"
                                )
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
    wait: bool = False,
    wait_timeout: int = 60,
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
                        f"[green]✓[/green] Power off signal sent to {hostname} ({mode_str})."
                    )
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} power state -> OFF...[/cyan]"
                        ):
                            if _wait_for_power_state(bmc, n, "OFF", timeout_sec=wait_timeout):
                                console.print(f"[bold green]✓ {hostname} is confirmed OFF.[/bold green]")
                            else:
                                console.print(
                                    f"[bold yellow]Timed out waiting for {hostname} to reach OFF.[/bold yellow]"
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
    wait: bool = False,
    wait_timeout: int = 60,
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
                    if wait:
                        with console.status(
                            f"[cyan]Waiting for {hostname} to cycle and reach ON...[/cyan]"
                        ):
                            time.sleep(3)
                            if _wait_for_power_state(bmc, n, "ON", timeout_sec=wait_timeout):
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


def _render_metrics_table(
    bmc: BMCController, settings: ClusterSettings, targets: list[int]
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

    total_current = 0
    total_avg = 0
    total_max = 0

    for n in targets:
        hostname = settings.get_hostname(n)
        metrics = bmc.get_power_metrics(n)
        pwr = metrics["power_state"]
        curr = metrics["current_watts"]
        avg = metrics["average_watts"]
        min_w = metrics["min_watts"]
        max_w = metrics["max_watts"]

        if curr is not None:
            total_current += curr
        if avg is not None:
            total_avg += avg
        if max_w is not None:
            total_max += max_w

        pwr_style = (
            "[bold green]ON[/bold green]"
            if pwr == "ON"
            else (
                "[dim]OFF[/dim]"
                if pwr == "OFF"
                else "[dim]UNKNOWN[/dim]"
            )
        )
        curr_str = (
            f"[bold green]{curr} W[/bold green]"
            if curr is not None
            else "[dim]-[/dim]"
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
            f"[bold green]{total_current} W ({total_current / 1000:.2f} kW)[/bold green]"
            if total_current > 0
            else "[dim]0 W[/dim]"
        )
        total_avg_str = (
            f"{total_avg} W ({total_avg / 1000:.2f} kW)"
            if total_avg > 0
            else "[dim]0 W[/dim]"
        )
        total_max_str = (
            f"[bold yellow]{total_max} W ({total_max / 1000:.2f} kW)[/bold yellow]"
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
    node: int | None = None,
    all_nodes: bool = False,
    watch: bool = False,
    interval: float = 2.0,
) -> None:
    if node is None and not all_nodes:
        targets = [1, 2, 3]
    else:
        try:
            targets = resolve_target_nodes(node, all_nodes)
        except ValueError as e:
            console.print(f"[bold red]{e}[/bold red]")
            return

    with BMCController(settings) as bmc:
        if watch:
            from rich.live import Live

            with Live(
                _render_metrics_table(bmc, settings, targets),
                console=console,
                refresh_per_second=1,
            ) as live:
                try:
                    while True:
                        time.sleep(interval)
                        live.update(
                            _render_metrics_table(bmc, settings, targets)
                        )
                except KeyboardInterrupt:
                    pass
        else:
            with console.status(
                "[cyan]Reading Redfish power telemetry...[/cyan]",
                spinner="dots",
            ):
                table = _render_metrics_table(bmc, settings, targets)
            console.print()
            console.print(table)
            console.print()
