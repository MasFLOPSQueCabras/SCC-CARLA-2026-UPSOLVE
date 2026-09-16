from rich.console import Console

from scc_carla.config import ClusterSettings
from scc_carla.db import reset_cluster_state
from scc_carla.http_server import EphemeralRangeHTTPServer

console = Console()


def down_command(settings: ClusterSettings, reset_db: bool = False) -> None:
    console.print("[cyan]Tearing down cluster resources...[/cyan]")
    EphemeralRangeHTTPServer.sweep_remote(
        settings.bastion_ssh_host, settings.bastion_http_port
    )
    console.print(
        f"[green]✓[/green] Swept RangeHTTPServer instances on port {settings.bastion_http_port}"
    )

    if reset_db:
        reset_cluster_state(settings.db_path)
        console.print("[green]✓[/green] Reset cluster node states in database")

    console.print("[bold green]Teardown complete. Zero lingering state.[/bold green]")
