from pathlib import Path

from rich.console import Console

from cabrita.bios import BiosProfile
from cabrita.commands.configure import configure_command
from cabrita.commands.deploy import deploy_command
from cabrita.config import ClusterSettings
from cabrita.db import (
    LockError,
    NodeLifecycle,
    ensure_db,
    get_all_nodes,
)
from cabrita.nodes import resolve_target_nodes
from cabrita.ops import cluster_lock

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
    cluster: Path | str | None = None,
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
                cluster=cluster,
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
                        cluster=cluster,
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
            "[dim]Tip: Use --force-lock to override or 'cabrita lock list' to view active locks.[/dim]"
        )
        return


from typing import Annotated

import typer


def up_cli(
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to provision (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
    pubkey: Annotated[
        Path | None, typer.Option("--pubkey", "-k", help="Path to SSH public key")
    ] = None,
    bios_profile: Annotated[
        BiosProfile,
        typer.Option(
            "--bios-profile",
            "-b",
            help="BIOS profile to configure (hpc, baseline, low_latency)",
        ),
    ] = BiosProfile.HPC,
    poll_timeout: Annotated[
        int,
        typer.Option(
            "--poll-timeout",
            "-t",
            help="Polling timeout in seconds for installation completion",
        ),
    ] = 1800,
    no_timeout: Annotated[
        bool,
        typer.Option(
            "--no-timeout",
            help="Disable polling timeout and wait indefinitely until installation completes",
        ),
    ] = False,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force-lock",
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-P",
            help="Node provider to use (libvirt, bmc, or chameleon)",
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            "--iso",
            "-i",
            help="Path or URL to OS image (.qcow2 cloud image or .iso installer)",
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
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Automatically approve execution plan without interactive confirmation",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show cluster execution plan diff and exit without applying changes",
        ),
    ] = False,
) -> None:
    """Preview cluster execution plan, prompt for confirmation, and provision cluster."""
    from cabrita.commands.plan import plan_command
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
        if image is None and manifest.defaults.os.cloud_image:
            image = (
                manifest.defaults.os.cloud_image_source
                or manifest.defaults.os.cloud_image
            )

    # 1. Render execution plan diff
    plan_command(cluster_path=manifest_file, settings=settings)

    if dry_run:
        console.print("[dim]Dry run complete. No changes were applied.[/dim]")
        raise typer.Exit(code=0)

    # 2. Prompt for explicit confirmation
    if not yes:
        confirmed = typer.confirm(
            "Do you want to perform these actions and start the cluster?",
            default=True,
        )
        if not confirmed:
            console.print("[yellow]Aborted by user.[/yellow]")
            raise typer.Exit(code=0)

    up_command(
        settings,
        node=node,
        pubkey_path=pubkey,
        poll_timeout=poll_timeout,
        bios_profile=bios_profile,
        no_timeout=no_timeout,
        force_lock=force_lock,
        provider=provider,
        image=image,
        cluster=manifest_file,
    )
