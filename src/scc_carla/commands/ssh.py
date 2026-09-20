import os
import shutil
import sys
from pathlib import Path

from rich.console import Console

from scc_carla.config import ClusterSettings
from scc_carla.http_server import is_running_on_bastion
from scc_carla.nodes import parse_node_target
from scc_carla.providers.factory import get_provider

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
