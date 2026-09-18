from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from scc_carla.bios import BiosProfile
from scc_carla.commands.bios import (
    bios_apply_command,
    bios_backup_command,
    bios_show_command,
)
from scc_carla.commands.down import down_command
from scc_carla.commands.power import (
    power_off_command,
    power_on_command,
    power_restart_command,
    power_status_command,
)
from scc_carla.commands.ssh import ssh_command
from scc_carla.commands.status import status_command
from scc_carla.commands.up import up_command
from scc_carla.config import get_settings
from scc_carla.db import break_lock, get_active_locks

console = Console()

app = typer.Typer(
    name="scc",
    help="SCC@CARLA Cluster Management CLI",
    no_args_is_help=True,
)

bios_app = typer.Typer(
    name="bios",
    help="HPE iLO BIOS Configuration and Inspection",
    no_args_is_help=True,
)
app.add_typer(bios_app, name="bios")

power_app = typer.Typer(
    name="power",
    help="Inspect and control bare-metal node power states",
    no_args_is_help=True,
)
app.add_typer(power_app, name="power")

lock_app = typer.Typer(
    name="lock",
    help="Inspect and manage cluster operational locks",
    no_args_is_help=True,
)
app.add_typer(lock_app, name="lock")


@app.command("up")
def up(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to provision (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Provision all nodes (1, 2, and 3)"),
    ] = False,
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
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
) -> None:
    settings = get_settings()
    up_command(
        settings,
        node=node,
        all_nodes=all_nodes,
        pubkey_path=pubkey,
        poll_timeout=poll_timeout,
        bios_profile=bios_profile,
        no_timeout=no_timeout,
        force=force,
    )


@app.command("down")
def down(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to decommission (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Decommission all nodes (1, 2, and 3)"),
    ] = False,
    reset_db: Annotated[
        bool,
        typer.Option("--reset-db", "-r", help="Reset node states in database"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
) -> None:
    settings = get_settings()
    down_command(
        settings,
        node=node,
        all_nodes=all_nodes,
        reset_db=reset_db,
        force=force,
    )


@app.command("status")
def status(
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Perform live hardware & network probing across cluster nodes",
        ),
    ] = True,
) -> None:
    settings = get_settings()
    status_command(settings, probe=probe)


@app.command("ssh")
def ssh_cli(
    node: Annotated[
        str | None,
        typer.Argument(
            help="Node target (1, 2, 3, node1, node2, node3, or bastion). Default: 1",
            show_default=False,
        ),
    ] = None,
    cmd: Annotated[
        list[str] | None,
        typer.Argument(
            help="Optional command to execute on remote host instead of interactive shell",
            show_default=False,
        ),
    ] = None,
    node_opt: Annotated[
        str | None,
        typer.Option(
            "--node",
            "-n",
            help="Node target (1, 2, 3, or bastion)",
        ),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            "-u",
            help="SSH username (default: cluster configured username)",
        ),
    ] = None,
    identity_file: Annotated[
        Path | None,
        typer.Option(
            "--identity-file",
            "-i",
            help="Path to SSH private key",
        ),
    ] = None,
    force_tty: Annotated[
        bool,
        typer.Option(
            "--tty",
            "-t",
            help="Force pseudo-terminal allocation (ssh -t)",
        ),
    ] = False,
) -> None:
    """Open an interactive SSH shell or execute commands on a cluster node or bastion."""
    settings = get_settings()

    if node_opt is not None:
        target = node_opt
        full_cmd = ([node] if node else []) + (cmd or [])
    else:
        target = node if node is not None else 1
        full_cmd = cmd or []

    ssh_command(
        settings=settings,
        target=target,
        command=full_cmd,
        user=user,
        identity_file=identity_file,
        force_tty=force_tty,
    )


@bios_app.command("show")
def bios_show(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to inspect (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Inspect all nodes (1, 2, and 3)"),
    ] = False,
) -> None:
    settings = get_settings()
    bios_show_command(settings, node=node, all_nodes=all_nodes)


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
    settings = get_settings()
    bios_backup_command(settings, node=node, output=output)


@bios_app.command("apply")
def bios_apply(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to configure (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Configure all nodes (1, 2, and 3)"),
    ] = False,
    profile: Annotated[
        BiosProfile,
        typer.Option(
            "--profile",
            "-p",
            help="BIOS profile to apply (hpc, baseline, low_latency)",
        ),
    ] = BiosProfile.HPC,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Override and break any conflicting operational locks",
        ),
    ] = False,
) -> None:
    settings = get_settings()
    bios_apply_command(
        settings,
        node=node,
        all_nodes=all_nodes,
        profile=profile,
        force=force,
    )


@lock_app.command("list")
def lock_list() -> None:
    """List all active and expired operational locks on the shared bastion database."""
    settings = get_settings()
    locks = get_active_locks(settings)
    if not locks:
        console.print("[dim]No operational locks are currently held.[/dim]")
        return

    table = Table(
        title="Shared Operational Locks",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Resource", justify="center", style="bold")
    table.add_column("Holder", justify="center")
    table.add_column("Operation", justify="center")
    table.add_column("Acquired At", justify="center")
    table.add_column("Elapsed", justify="center")
    table.add_column("Timeout", justify="center")
    table.add_column("Status", justify="center")

    for lk in locks:
        elapsed_m = lk.elapsed_sec // 60
        elapsed_s = lk.elapsed_sec % 60
        elapsed_str = f"{elapsed_m}m {elapsed_s}s"
        status_str = (
            "[bold red]EXPIRED[/bold red]"
            if lk.is_expired
            else "[bold yellow]LOCKED[/bold yellow]"
        )
        table.add_row(
            lk.resource,
            lk.holder,
            lk.operation,
            lk.acquired_at,
            elapsed_str,
            f"{lk.timeout_sec}s",
            status_str,
        )

    console.print(table)


@lock_app.command("release")
def lock_release(
    resource: Annotated[
        str,
        typer.Argument(
            help="Resource lock to release/break ('node-1', 'node-2', 'node-3', 'cluster', or 'all')"
        ),
    ],
) -> None:
    """Release or break an operational lock on a resource or all resources."""
    settings = get_settings()
    break_lock(settings, resource)
    console.print(f"[bold green]✓ Released lock on '{resource}'.[/bold green]")


@power_app.command("on")
def power_on(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to power on (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Power on all nodes (1, 2, and 3)"),
    ] = False,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Override and break conflicting operational locks",
        ),
    ] = False,
) -> None:
    """Power on bare-metal cluster node(s) via BMC."""
    settings = get_settings()
    power_on_command(
        settings, node=node, all_nodes=all_nodes, force_lock=force_lock
    )


@power_app.command("off")
def power_off(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to power off (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Power off all nodes (1, 2, and 3)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Force immediate hardware power off (ForceOff) instead of graceful shutdown",
        ),
    ] = False,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force-lock",
            help="Override and break conflicting operational locks",
        ),
    ] = False,
) -> None:
    """Power off bare-metal cluster node(s) gracefully or forcefully."""
    settings = get_settings()
    power_off_command(
        settings,
        node=node,
        all_nodes=all_nodes,
        graceful=not force,
        force_lock=force_lock,
    )


@power_app.command("restart")
def power_restart(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to restart (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Restart all nodes (1, 2, and 3)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Force immediate hard reboot (ForceRestart) instead of graceful restart",
        ),
    ] = False,
    force_lock: Annotated[
        bool,
        typer.Option(
            "--force-lock",
            help="Override and break conflicting operational locks",
        ),
    ] = False,
) -> None:
    """Reboot / restart bare-metal cluster node(s) gracefully or forcefully."""
    settings = get_settings()
    power_restart_command(
        settings,
        node=node,
        all_nodes=all_nodes,
        graceful=not force,
        force_lock=force_lock,
    )


@power_app.command("status")
def power_status(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to inspect (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Inspect all nodes (1, 2, and 3)"),
    ] = False,
) -> None:
    """Inspect current bare-metal BMC power state across cluster nodes."""
    settings = get_settings()
    power_status_command(settings, node=node, all_nodes=all_nodes)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
