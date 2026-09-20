from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from scc_carla.bios import BiosProfile, get_profile_attributes
from scc_carla.bmc import BMCController
from scc_carla.config import ClusterSettings
from scc_carla.db import LockError, update_node_state
from scc_carla.nodes import resolve_target_nodes
from scc_carla.ops import cluster_lock

console = Console()

KEY_BIOS_ATTRIBUTES = [
    "WorkloadProfile",
    "PowerRegulator",
    "EnergyPerfBias",
    "EnergyEfficientTurbo",
    "ProcTurbo",
    "ProcHyperthreading",
    "NumaGroupSizeOpt",
    "SubNumaClustering",
    "UncoreFreqScaling",
    "MinProcIdlePower",
]


def bios_show_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
) -> None:
    if settings.provider not in ("bmc", "helvetios"):
        console.print(
            f"[yellow]BIOS operations are not supported for provider '{settings.provider}'.[/yellow]"
        )
        return

    if BMCController is None:
        console.print(
            "[bold red]Helvetios provider dependencies are not installed.[/bold red]\n"
            "Run 'uv sync --extra helvetios' to enable BIOS management."
        )
        return

    try:
        target_nodes = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    with BMCController(settings) as bmc:
        for n in target_nodes:
            hostname = settings.get_hostname(n)
            with console.status(
                f"[cyan]Querying BIOS settings for {hostname}...[/cyan]"
            ):
                active = bmc.get_bios_settings(n)
                pending = bmc.get_pending_bios_settings(n)

            if not active:
                console.print(
                    f"[bold red]Failed to retrieve BIOS settings for {hostname} ({settings.get_bmc_ip(n)})[/bold red]"
                )
                continue

            table = Table(
                title=f"BIOS Configuration: {hostname} ({settings.get_bmc_ip(n)})",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Attribute", justify="left", style="bold")
            table.add_column("Active Value", justify="center")
            table.add_column("Pending (Staged) Value", justify="center")

            for attr in KEY_BIOS_ATTRIBUTES:
                active_val = str(active.get(attr, "-"))
                pending_val = str(pending.get(attr, "-"))
                if pending_val != "-":
                    pending_display = f"[bold yellow]{pending_val}[/bold yellow]"
                else:
                    pending_display = "[dim]-[/dim]"
                table.add_row(attr, active_val, pending_display)

            console.print()
            console.print(table)
            console.print()


def bios_backup_command(
    settings: ClusterSettings,
    node: int = 1,
    output: Path | None = None,
) -> None:
    if settings.provider not in ("bmc", "helvetios"):
        console.print(
            f"[yellow]BIOS operations are not supported for provider '{settings.provider}'.[/yellow]"
        )
        return

    if BMCController is None:
        console.print(
            "[bold red]Helvetios provider dependencies are not installed.[/bold red]\n"
            "Run 'uv sync --extra helvetios' to enable BIOS management."
        )
        return

    hostname = settings.get_hostname(node)
    dest_path = output or Path(f"bios_{hostname}_backup.json")

    with (
        BMCController(settings) as bmc,
        console.status(f"[cyan]Backing up full BIOS settings for {hostname}...[/cyan]"),
    ):
        attrs = bmc.get_bios_settings(node)
        if not attrs:
            console.print(
                f"[bold red]Failed to fetch BIOS settings for {hostname}.[/bold red]"
            )
            return

        saved_path = bmc.backup_bios(node, dest_path)
        console.print(
            f"[green]✓[/green] Successfully backed up {len(attrs)} BIOS attributes for {hostname} to [bold]{saved_path}[/bold]"
        )


def _stage_node_bios(
    bmc: BMCController,
    settings: ClusterSettings,
    node: int,
    profile_val: str,
    attrs: dict[str, Any],
) -> None:
    hostname = settings.get_hostname(node)
    with console.status(
        f"[cyan]Staging '{profile_val}' BIOS profile on {hostname}...[/cyan]"
    ):
        success = bmc.set_bios_settings(node, attrs)

    if success:
        update_node_state(settings, node, bios_profile=profile_val)
        console.print(
            f"[green]✓[/green] Staged '{profile_val}' profile on {hostname}. "
            "[dim](Changes take effect after next reboot)[/dim]"
        )
    else:
        console.print(
            f"[bold red]Failed to stage BIOS profile on {hostname}.[/bold red]"
        )


def bios_apply_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    profile: BiosProfile = BiosProfile.HPC,
    force_lock: bool = False,
) -> None:
    if settings.provider not in ("bmc", "helvetios"):
        console.print(
            f"[yellow]BIOS operations are not supported for provider '{settings.provider}'.[/yellow]"
        )
        return

    if BMCController is None:
        console.print(
            "[bold red]Helvetios provider dependencies are not installed.[/bold red]\n"
            "Run 'uv sync --extra helvetios' to enable BIOS management."
        )
        return

    try:
        target_nodes = resolve_target_nodes(node)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return

    attrs: dict[str, Any] = get_profile_attributes(profile)

    try:
        with (
            cluster_lock(
                settings,
                targets=target_nodes,
                operation=f"bios-apply-{profile.value}",
                force=force_lock,
            ),
            BMCController(settings) as bmc,
        ):
            for n in target_nodes:
                _stage_node_bios(bmc, settings, n, profile.value, attrs)
    except LockError:
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return


from typing import Annotated

import typer

bios_app = typer.Typer(
    name="bios",
    help="HPE iLO BIOS Configuration and Inspection",
    no_args_is_help=True,
)


@bios_app.command("show")
def bios_show(
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to inspect (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
) -> None:
    """Inspect active and pending BIOS settings via BMC."""
    from scc_carla.config import get_settings

    settings = get_settings()
    bios_show_command(settings, node=node)


@bios_app.command("backup")
def bios_backup(
    node: Annotated[
        int,
        typer.Option("--node", "-n", help="Node ID to backup (1, 2, or 3)"),
    ] = 1,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output JSON path for backup"),
    ] = None,
) -> None:
    """Export complete BIOS attribute JSON dump for a node."""
    from scc_carla.config import get_settings

    settings = get_settings()
    bios_backup_command(settings, node=node, output=output)


@bios_app.command("apply")
def bios_apply(
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to configure (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
    profile: Annotated[
        BiosProfile,
        typer.Option(
            "--profile",
            "-p",
            help="BIOS profile to apply (hpc, baseline, low_latency)",
        ),
    ] = BiosProfile.HPC,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force-lock",
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
) -> None:
    """Stage a pre-tuned BIOS profile on target nodes."""
    from scc_carla.config import get_settings

    settings = get_settings()
    bios_apply_command(
        settings,
        node=node,
        profile=profile,
        force_lock=force_lock,
    )
