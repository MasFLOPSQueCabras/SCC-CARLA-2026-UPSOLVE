import os
import shutil
import sys
from pathlib import Path

from rich.console import Console

from cabrita.config import ClusterSettings
from cabrita.http_server import is_running_on_bastion
from cabrita.nodes import parse_node_target
from cabrita.providers.factory import get_provider

console = Console()


def ssh_command(
    settings: ClusterSettings,
    target: int | str | None = None,
    command: list[str] | None = None,
    user: str | None = None,
    identity_file: Path | None = None,
    force_tty: bool = False,
    provider: str | None = None,
) -> None:
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        console.print("[bold red]SSH executable not found in PATH.[/bold red]")
        sys.exit(1)

    try:
        node_id = parse_node_target(target)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)

    on_bastion = is_running_on_bastion(settings.bastion_hostname)

    ssh_args: list[str] = [ssh_bin]

    # Resolve identity file if available
    key_path = identity_file
    if key_path is None:
        default_key = Path.home() / ".ssh" / "carla_scc_ed25519"
        if default_key.exists():
            key_path = default_key

    if key_path is not None:
        ssh_args.extend(["-i", str(key_path)])

    # Avoid blocking or warning on re-imaged node host keys
    ssh_args.extend(
        [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
        ]
    )

    if force_tty:
        ssh_args.append("-t")

    if node_id == 0:
        # Target is bastion
        if on_bastion:
            console.print(
                "[bold yellow]Already running on the bastion host.[/bold yellow]"
            )
            return
        ssh_args.append(settings.bastion_ssh_host)
    else:
        # Target is a cluster node (1, 2, or 3)
        with get_provider(settings, provider) as prov:
            node_ip = prov.get_node_ip(node_id)
            node_user = user or settings.node_username

            if prov.paths.bastion_ssh_host:
                ssh_args.extend(["-J", prov.paths.bastion_ssh_host])

            ssh_args.append(f"{node_user}@{node_ip}")

    # Append command arguments if provided
    if command:
        ssh_args.extend(command)

    # Exec OpenSSH directly to replace current process for native TTY & signal handling
    try:
        os.execvp(ssh_bin, ssh_args)
    except OSError as e:
        console.print(f"[bold red]Failed to execute ssh: {e}[/bold red]")
        sys.exit(1)


from typing import Annotated

import typer


def ssh_cli(
    node: Annotated[
        str | None,
        typer.Argument(
            help="Target node to connect to: 1, 2, 3, or 'bastion' / 0. Defaults to node 1.",
        ),
    ] = "1",
    command: Annotated[
        list[str] | None,
        typer.Argument(
            help="Optional remote command and arguments to execute non-interactively on target",
        ),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", "-u", help="SSH username override"),
    ] = None,
    identity_file: Annotated[
        Path | None,
        typer.Option(
            "--identity",
            "-i",
            help="Path to custom private SSH key file",
        ),
    ] = None,
    force_tty: Annotated[
        bool,
        typer.Option(
            "-t",
            help="Force pseudo-terminal allocation (useful for interactive screen/tmux over SSH)",
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
) -> None:
    """Connect to a cluster node or the bastion via native SSH."""
    from cabrita.config import get_settings

    settings = get_settings()
    ssh_command(
        settings,
        target=node,
        command=command,
        user=user,
        identity_file=identity_file,
        force_tty=force_tty,
        provider=provider,
    )
