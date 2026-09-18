from pathlib import Path
from typing import Annotated

import typer

from scc_carla.bios import BiosProfile
from scc_carla.commands.bios import (
    bios_apply_command,
    bios_backup_command,
    bios_show_command,
)
from scc_carla.commands.down import down_command
from scc_carla.commands.ssh import ssh_command
from scc_carla.commands.status import status_command
from scc_carla.commands.up import up_command
from scc_carla.config import get_settings

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
) -> None:
    settings = get_settings()
    down_command(settings, node=node, all_nodes=all_nodes, reset_db=reset_db)


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
) -> None:
    settings = get_settings()
    bios_apply_command(settings, node=node, all_nodes=all_nodes, profile=profile)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
