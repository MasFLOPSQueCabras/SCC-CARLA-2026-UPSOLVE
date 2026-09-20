from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

from scc_carla.config import ClusterSettings
from scc_carla.db import (
    LockError,
    NodeLifecycle,
    reset_cluster_state,
    update_node_state,
)
from scc_carla.http_server import EphemeralRangeHTTPServer
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock
from scc_carla.providers.factory import get_provider
from scc_carla.templating import TemplateEngine

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

                with ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
                    futures = [executor.submit(_decommission_node, n) for n in targets]
                    for f in as_completed(futures):
                        f.result()

            # Only sweep shared bastion HTTP resources if tearing down all nodes under provider with remote serve
            should_sweep = (
                reset_db or len(targets) == 3
            ) and prov.paths.remote_serve_dir is not None
            if should_sweep:
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
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return

    console.print("[bold green]Teardown complete. Zero lingering state.[/bold green]")


from pathlib import Path
from typing import Annotated

import typer


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
            help="Path to cluster manifest or values.yaml override file",
        ),
    ] = None,
) -> None:
    """Tear down and decommission cluster node(s)."""
    from scc_core.manifest import load_manifest

    from scc_carla.config import get_settings

    settings = get_settings()

    manifest_file = cluster or (
        Path.cwd() / "values.yaml" if (Path.cwd() / "values.yaml").exists() else None
    )
    if manifest_file and manifest_file.exists():
        manifest = load_manifest(manifest_file)
        if provider is None:
            provider = manifest.provider

    down_command(
        settings,
        node=node,
        reset_db=reset_db,
        force_lock=force_lock,
        provider=provider,
    )
