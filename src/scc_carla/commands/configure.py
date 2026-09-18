import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from scc_carla.config import ClusterSettings

console = Console()


def configure_command(
    settings: ClusterSettings,
    playbook: str = "site.yaml",
    limit: str | None = None,
    check: bool = False,
    tags: str | None = None,
) -> None:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    ansible_dir = project_root / "ansible"
    cfg_file = ansible_dir / "ansible.cfg"
    inv_file = ansible_dir / "inventory" / "hosts.yaml"
    pb_file = ansible_dir / "playbooks" / playbook

    if not pb_file.exists():
        console.print(f"[bold red]Playbook not found: {pb_file}[/bold red]")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "ansible.cli.playbook",
        "-i",
        str(inv_file),
        str(pb_file),
    ]
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    if tags:
        cmd.extend(["--tags", tags])

    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = str(cfg_file)

    console.print(f"[cyan]Executing Ansible playbook '{playbook}'...[/cyan]")
    res = subprocess.run(cmd, env=env, check=False)
    if res.returncode != 0:
        console.print(f"[bold red]Playbook execution failed with exit code {res.returncode}.[/bold red]")
        sys.exit(res.returncode)
    console.print("[green]✓[/green] [bold green]Ansible configuration complete.[/bold green]")
