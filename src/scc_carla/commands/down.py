from rich.console import Console

from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import (
    ClusterLock,
    LockError,
    NodeLifecycle,
    reset_cluster_state,
    update_node_state,
)
from scc_carla.http_server import EphemeralRangeHTTPServer
from scc_carla.nodes import resolve_target_nodes

console = Console()


def down_command(
    settings: ClusterSettings,
    node: int | None = None,
    all_nodes: bool = False,
    reset_db: bool = False,
    force: bool = False,
) -> None:
    try:
        targets = resolve_target_nodes(node, all_nodes)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    resources: list[str] = []
    if targets:
        resources = [f"node-{n}" for n in targets]
    elif reset_db:
        resources = ["cluster"]

    try:
        with ClusterLock(
            settings, resources=resources, operation="down", force=force
        ):
            if targets:
                with BMCController(settings) as bmc:
                    for n in targets:
                        hostname = settings.get_hostname(n)
                        console.print(
                            f"[cyan]Decommissioning {hostname}...[/cyan]"
                        )
                        bmc.eject_virtual_media(n)
                        console.print(
                            f"[green]✓[/green] Virtual Media ejected on {hostname}"
                        )

                        bmc.power_off(n)
                        console.print(f"[green]✓[/green] {hostname} powered off")

                        update_node_state(settings, n, NodeLifecycle.OFFLINE)
                        console.print(
                            f"[bold green]{hostname} is offline and decommissioned.[/bold green]"
                        )

            console.print("[cyan]Sweeping cluster background resources...[/cyan]")
            EphemeralRangeHTTPServer.sweep_remote(
                settings.bastion_ssh_host, settings.bastion_http_port
            )
            console.print(
                f"[green]✓[/green] Swept RangeHTTPServer instances on port {settings.bastion_http_port}"
            )

            if reset_db:
                reset_cluster_state(settings)
                console.print(
                    "[green]✓[/green] Reset cluster node states in database"
                )

    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return

    console.print(
        "[bold green]Teardown complete. Zero lingering state.[/bold green]"
    )

