import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from scc_carla.config import ClusterSettings
from scc_carla.nodes import resolve_target_nodes

console = Console()


def configure_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    playbook: str = "site.yaml",
    limit: str | None = None,
    check: bool = False,
    tags: str | None = None,
    exit_on_error: bool = True,
) -> bool:
    """Executes Ansible playbooks across targeted cluster nodes.

    Decouples node-independent setup (common, infiniband, hpc_tune, nfs, spack)
    from cluster-wide coordination (cluster_ssh, mpi hostfile).
    """
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    ansible_dir = project_root / "ansible"
    cfg_file = ansible_dir / "ansible.cfg"
    inv_file = ansible_dir / "inventory" / "hosts.yaml"
    pb_file = ansible_dir / "playbooks" / playbook

    if not pb_file.exists():
        console.print(f"[bold red]Playbook not found: {pb_file}[/bold red]")
        if exit_on_error:
            sys.exit(1)
        return False

    targets: list[int] = []
    if node is not None:
        try:
            targets = resolve_target_nodes(node)
        except ValueError as e:
            console.print(f"[bold red]{e}[/bold red]")
            if exit_on_error:
                sys.exit(1)
            return False

    effective_limit = limit
    if targets:
        target_hosts = ",".join(settings.get_hostname(n) for n in targets)
        effective_limit = f"{limit},{target_hosts}" if limit else target_hosts

    # If targeting a strict subset of cluster nodes and no explicit tag given,
    # default to node_independent play to avoid failing on cluster-wide coordination
    effective_tags = tags
    if targets and len(targets) < 3 and tags is None and playbook == "site.yaml":
        effective_tags = "node_independent"
        console.print(
            f"[dim]Targeting subset ({target_hosts}): running node_independent roles.[/dim]"
        )

    cmd = [
        sys.executable,
        "-m",
        "ansible.cli.playbook",
        "-i",
        str(inv_file),
        str(pb_file),
    ]
    if effective_limit:
        cmd.extend(["--limit", effective_limit])
    if check:
        cmd.append("--check")
    if effective_tags:
        cmd.extend(["--tags", effective_tags])

    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = str(cfg_file)

    console.print(f"[cyan]Executing Ansible playbook '{playbook}'...[/cyan]")
    res = subprocess.run(cmd, env=env, check=False)
    if res.returncode != 0:
        console.print(
            f"[bold red]Playbook execution failed with exit code {res.returncode}.[/bold red]"
        )
        if exit_on_error:
            sys.exit(res.returncode)
        return False

    console.print(
        "[green]✓[/green] [bold green]Ansible configuration complete.[/bold green]"
    )
    return True
