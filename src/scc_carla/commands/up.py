from rich.console import Console

from scc_carla.config import ClusterSettings
from scc_carla.db import ensure_db

console = Console()


def up_command(settings: ClusterSettings) -> None:
    console.print("[cyan]Initializing cluster state...[/cyan]")
    ensure_db(settings.db_path, settings.team_id)
    console.print("[green]✓[/green] State database ready")
    console.print("[bold green]Cluster foundation is up.[/bold green]")
