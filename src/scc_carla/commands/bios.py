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


def bios_apply_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    profile: BiosProfile = BiosProfile.HPC,
    force_lock: bool = False,
) -> None:
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
                hostname = settings.get_hostname(n)
                with console.status(
                    f"[cyan]Staging '{profile.value}' BIOS profile on {hostname}...[/cyan]"
                ):
                    success = bmc.set_bios_settings(n, attrs)

                if success:
                    update_node_state(settings, n, bios_profile=profile.value)
                    console.print(
                        f"[green]✓[/green] Staged '{profile.value}' profile on {hostname}. "
                        "[dim](Changes take effect after next reboot)[/dim]"
                    )
                else:
                    console.print(
                        f"[bold red]Failed to stage BIOS profile on {hostname}.[/bold red]"
                    )
    except LockError as e:
        console.print(f"[bold red]Lock conflict: {e}[/bold red]")
        console.print(
            "[dim]Tip: Use --force-lock to override or 'scc-carla lock list' to view active locks.[/dim]"
        )
        return
