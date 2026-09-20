from pathlib import Path

from rich.console import Console

from scc_carla.bios import BiosProfile
from scc_carla.commands.configure import configure_command
from scc_carla.commands.deploy import deploy_command
from scc_carla.config import ClusterSettings
from scc_carla.db import (
    LockError,
    NodeLifecycle,
    ensure_db,
    get_all_nodes,
)
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock

console = Console()


def up_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    pubkey_path: Path | None = None,
    poll_timeout: int = 1800,
    bios_profile: BiosProfile = BiosProfile.HPC,
    no_timeout: bool = False,
    force_lock: bool = False,
    provider: str | None = None,
    image: str | None = None,
    iso: str | None = None,
) -> None:
    """Performs whole lifecycle cluster startup: OS deployment, Ansible configuration, and cluster coordination."""
    ensure_db(settings)

    try:
        target_nodes = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    try:
        with cluster_lock(
            settings,
            targets=target_nodes,
            operation="up",
            force=force_lock,
        ):
            console.print(
                f"[cyan]Initiating whole lifecycle startup across {len(target_nodes)} node(s)...[/cyan]"
            )

            # Step 1: Deploy baremetal OS image & wait for SSH readiness
            deploy_ok = deploy_command(
                settings=settings,
                node=target_nodes,
                pubkey_path=pubkey_path,
                poll_timeout=poll_timeout,
                bios_profile=bios_profile,
                no_timeout=no_timeout,
                force_lock=force_lock,
                provider=provider,
                image=image,
                iso=iso,
                acquire_lock=False,
                run_ansible=False,
            )
            if not deploy_ok:
                console.print(
                    "[bold red]Lifecycle startup aborted: OS deployment failed on one or more nodes.[/bold red]"
                )
                return

            # Step 2: Configure deployed nodes with Ansible
            console.print(
                "\n[cyan]OS deployment complete. Running Ansible configuration across deployed nodes...[/cyan]"
            )
            config_ok = configure_command(
                settings,
                node=target_nodes,
                exit_on_error=False,
                provider=provider,
            )
            if not config_ok:
                console.print(
                    "[bold yellow]⚠ Ansible configuration finished with warnings.[/bold yellow]"
                )

            # Step 3: Run cluster-wide coordination if all 3 nodes are online
            try:
                all_cluster_nodes = get_all_nodes(settings)
                all_3_online = len(all_cluster_nodes) == 3 and all(
                    cn.state in (NodeLifecycle.READY, NodeLifecycle.BOOTSTRAPPED)
                    for cn in all_cluster_nodes
                )
                if all_3_online:
                    console.print(
                        "\n[cyan]All 3 cluster nodes online! Running cluster-wide coordination (SSH trust, MPI hostfile, InfiniBand)...[/cyan]"
                    )
                    coord_ok = configure_command(
                        settings,
                        tags="cluster_coordination",
                        exit_on_error=False,
                        provider=provider,
                    )
                    if coord_ok:
                        console.print(
                            "[bold green]✓ Full 3-node cluster coordinated and verified for MPI/HPL.[/bold green]"
                        )
                    else:
                        console.print(
                            "[bold yellow]⚠ Cluster coordination completed with non-fatal warnings.[/bold yellow]"
                        )
                else:
                    ready_count = sum(
                        1
                        for cn in all_cluster_nodes
                        if cn.state in (NodeLifecycle.READY, NodeLifecycle.BOOTSTRAPPED)
                    )
                    console.print(
                        f"\n[dim]Active nodes: {ready_count}/3. Cluster-wide coordination will run when all 3 nodes are online.[/dim]"
                    )
            except Exception as e:  # noqa: BLE001
                console.print(
                    f"[dim]Could not evaluate cluster coordination: {e}[/dim]"
                )

    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return
