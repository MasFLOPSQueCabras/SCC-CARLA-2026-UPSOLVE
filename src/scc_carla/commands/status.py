from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scc_carla.config import ClusterSettings
from scc_carla.db import NodeLifecycle, get_all_nodes

console = Console()


def status_command(settings: ClusterSettings) -> None:
    nodes = get_all_nodes(settings.db_path, settings.team_id)

    table = Table(
        title="SCC@CARLA Cluster Nodes",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Node ID", justify="center", style="bold")
    table.add_column("Hostname", justify="center")
    table.add_column("OS IP", justify="center")
    table.add_column("BMC IP", justify="center")
    table.add_column("State", justify="center")
    table.add_column("BIOS Profile", justify="center")
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

        table.add_row(
            str(node.node_id),
            node.hostname,
            node.os_ip,
            node.bmc_ip,
            state_style,
            node.bios_profile or "factory_baseline",
            node.last_updated or "-",
        )

    console.print()
    console.print(table)
    console.print(
        Panel(
            f"[bold]Cluster Configuration[/bold]\n"
            f"• Team ID: {settings.team_id}\n"
            f"• Gateway: {settings.gateway_ip}\n"
            f"• Bastion HTTP: {settings.bastion_http_ip}:{settings.bastion_http_port}\n"
            f"• State Database: {settings.db_path}",
            title="Cluster Info",
            border_style="dim",
        )
    )
    console.print()
