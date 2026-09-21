from rich.console import Console

from cabrita.config import ClusterSettings
from cabrita.core.parallel import ParallelRunner
from cabrita.db import (
    LockError,
    NodeLifecycle,
    reset_cluster_state,
    update_node_state,
)
from cabrita.nodes import resolve_target_nodes
from cabrita.ops import cluster_lock
from cabrita.providers.factory import get_provider
from cabrita.templating import TemplateEngine

console = Console()


def down_command(
    settings: ClusterSettings,
    node: list[int] | int | None = None,
    reset_db: bool = False,
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
            cluster_lock(settings, targets=targets, operation="down", force=force_lock),
            get_provider(settings, provider) as prov,
        ):
            if targets:

                def _decommission_node(n: int) -> None:
                    hostname = settings.get_hostname(n)
                    console.print(f"[cyan]Decommissioning {hostname}...[/cyan]")
                    prov.teardown_node(n)
                    update_node_state(settings, n, NodeLifecycle.OFFLINE)
                    console.print(
                        f"[bold green]{hostname} is offline and decommissioned.[/bold green]"
                    )

                runner = ParallelRunner[int, None](
                    max_workers=max(1, len(targets)),
                    timeout_sec=120.0,
                    thread_name_prefix="cabrita-down",
                )
                runner.items(targets).task(_decommission_node).run()

            # Only sweep shared bastion HTTP resources if tearing down all nodes under provider with remote serve
            should_sweep = (
                reset_db or len(targets) == 3
            ) and prov.paths.remote_serve_dir is not None
            if should_sweep:
                from cabrita.providers.helvetios.media_server import (
                    EphemeralRangeHTTPServer,
                )

                template_engine = TemplateEngine()
                console.print("[cyan]Sweeping cluster background resources...[/cyan]")
                swept = EphemeralRangeHTTPServer.sweep_remote(
                    settings.bastion_ssh_host,
                    settings.bastion_http_port,
                    template_engine=template_engine,
                    force=reset_db,
                )
                if swept:
                    console.print(
                        f"[green]✓[/green] Swept RangeHTTPServer instances on port {settings.bastion_http_port}"
                    )

            if reset_db:
                reset_cluster_state(settings)
                console.print("[green]✓[/green] Reset cluster node states in database")

    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'cabrita lock list' to view active locks.[/dim]"
        )
        return

    console.print("[bold green]Teardown complete. Zero lingering state.[/bold green]")


from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table


def render_teardown_plan(
    settings: ClusterSettings,
    targets: list[int],
    reset_db: bool = False,
    provider: str | None = None,
) -> None:
    """Computes and displays a teardown execution plan showing what resources will be affected."""
    selected_provider = provider or settings.provider
    console.print(
        Panel.fit(
            f"[bold cyan]Action:[/bold cyan] Teardown & Decommission\n"
            f"[bold cyan]Target Nodes:[/bold cyan] {targets} ({len(targets)} node(s))\n"
            f"[bold cyan]Provider:[/bold cyan] {selected_provider} | "
            f"[bold cyan]Reset Database:[/bold cyan] {reset_db}",
            title="[bold red]SCC Cluster Teardown Plan[/bold red]",
            border_style="red",
        )
    )

    table = Table(
        title="Resources Scheduled for Teardown",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Resource", style="bold", min_width=20)
    table.add_column("Type", style="dim", min_width=14)
    table.add_column("Current State", min_width=24)
    table.add_column("Target State", min_width=24)
    table.add_column("Action", justify="center", min_width=12)

    for n in targets:
        hostname = settings.get_hostname(n)
        table.add_row(
            f"Node '{hostname}'",
            "Compute Node",
            "Active / Provisioned",
            "Powered Off & Decommissioned",
            "[bold red]- DESTROY[/bold red]",
        )
        if selected_provider in ("vm", "libvirt"):
            table.add_row(
                f"Disk Overlay '{hostname}.qcow2'",
                "CoW Storage",
                "Allocated overlay",
                "Teardown / Discarded",
                "[bold red]- DESTROY[/bold red]",
            )

    table.add_row(
        "Database Node States",
        "Turso DB",
        "Current lifecycle",
        "RESET TO UNPROVISIONED" if reset_db else "OFFLINE",
        "[bold yellow]~ UPDATE[/bold yellow]",
    )

    table.add_row(
        "Cluster Locks",
        "Lock",
        "Exclusive lease for teardown",
        "Released (Unlocked)",
        "[bold cyan]* TASK[/bold cyan]",
    )

    console.print(table)
    console.print(
        f"\n[bold]Teardown Plan:[/bold] "
        f"[red]{len(targets) * (2 if selected_provider in ('vm', 'libvirt') else 1)} to destroy/stop[/red], "
        f"[yellow]1 database state update[/yellow], "
        f"[cyan]1 operational task[/cyan].\n"
    )


def down_cli(
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to decommission (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
    reset_db: Annotated[
        bool,
        typer.Option("--reset-db", "-r", help="Reset node states in database"),
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
    cluster: Annotated[
        Path | None,
        typer.Option(
            "--cluster",
            "-c",
            help="Path to cluster manifest or cluster.yaml override file",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Automatically approve teardown plan without interactive confirmation",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show teardown plan diff and exit without applying changes",
        ),
    ] = False,
) -> None:
    """Preview teardown plan, prompt for confirmation, and decommission cluster node(s)."""
    from cabrita.config import get_settings
    from cabrita.core.manifest import load_manifest

    settings = get_settings()

    manifest_file = cluster or (
        Path.cwd() / "cluster.yaml" if (Path.cwd() / "cluster.yaml").exists() else None
    )
    if manifest_file and manifest_file.exists():
        manifest = load_manifest(manifest_file)
        if provider is None:
            provider = manifest.provider

    try:
        targets = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(code=1) from e

    # 1. Render teardown plan
    render_teardown_plan(settings, targets, reset_db=reset_db, provider=provider)

    if dry_run:
        console.print("[dim]Dry run complete. No resources were modified.[/dim]")
        raise typer.Exit(code=0)

    # 2. Prompt for explicit confirmation
    if not yes:
        confirmed = typer.confirm(
            f"Are you sure you want to shut down and tear down {len(targets)} node(s)?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Aborted by user.[/yellow]")
            raise typer.Exit(code=0)

    down_command(
        settings,
        node=targets,
        reset_db=reset_db,
        force_lock=force_lock,
        provider=provider,
    )
