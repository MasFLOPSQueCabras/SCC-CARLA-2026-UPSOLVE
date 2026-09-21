import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from cabrita.config import ClusterSettings
from cabrita.db import NodeLifecycle, update_node_state
from cabrita.nodes import resolve_target_nodes
from cabrita.providers.factory import get_provider
from cabrita.ssh import is_ssh_authenticated

console = Console()


def configure_command(
    settings: ClusterSettings,
    node: int | list[int] | None = None,
    playbook: str = "site.yaml",
    limit: str | None = None,
    check: bool = False,
    tags: str | None = None,
    exit_on_error: bool = True,
    provider: str | None = None,
    cluster: Path | str | None = None,
) -> bool:
    """Executes Ansible playbooks across targeted cluster nodes.

    Verifies machine reachability first; if no machines are available, informs the user.
    When machines are available, scopes configuration to the reachable subset.
    """
    ansible_dir = (Path.cwd() / "ansible").resolve()
    if not ansible_dir.is_dir():
        console.print(
            f"[bold red]Ansible recipes directory not found: {ansible_dir}[/bold red]\n"
            "[dim]Run 'cabrita init' first to scaffold workspace recipes.[/dim]"
        )
        if exit_on_error:
            sys.exit(1)
        return False

    cfg_file = ansible_dir / "ansible.cfg"
    inv_file = ansible_dir / "inventory" / "dynamic_inventory.py"
    if not inv_file.exists():
        inv_file = ansible_dir / "inventory" / "hosts.yaml"
    pb_file = ansible_dir / "playbooks" / playbook

    if not pb_file.exists():
        console.print(f"[bold red]Playbook not found: {pb_file}[/bold red]")
        if exit_on_error:
            sys.exit(1)
        return False

    # Auto-install required Ansible collections if missing
    req_file = ansible_dir / "requirements.yml"
    if req_file.exists():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ansible.cli.galaxy",
                "collection",
                "install",
                "-r",
                str(req_file),
            ],
            check=False,
            capture_output=True,
            timeout=45,
        )

    targets = resolve_target_nodes(node)

    with get_provider(settings, provider) as prov:
        key_file = Path.home() / ".ssh" / "carla_scc_ed25519"
        key_path = key_file if key_file.exists() else None

        # Check which machines are actually available and reachable via SSH
        available_nodes: list[int] = []
        for n in targets:
            ip = prov.get_node_ip(n)
            if is_ssh_authenticated(
                ip,
                settings.node_username,
                bastion_ssh_host=prov.paths.bastion_ssh_host,
                key_path=key_path,
                timeout=3,
            ):
                available_nodes.append(n)

        if not available_nodes:
            console.print(
                "[bold yellow]No cluster nodes are currently online or reachable via SSH.[/bold yellow]\n"
                "[dim]Please deploy machines first using 'cabrita deploy' or 'cabrita up'.[/dim]"
            )
            if exit_on_error:
                sys.exit(1)
            return False

        available_hostnames = [
            settings.get_hostname(n) for n in sorted(available_nodes)
        ]
        console.print(
            f"[green]Available machine(s):[/green] [bold]{', '.join(available_hostnames)}[/bold]"
        )

        effective_limit = limit
        target_hosts = ",".join(available_hostnames)
        effective_limit = f"{limit},{target_hosts}" if limit else target_hosts

        effective_tags = tags
        if len(available_nodes) < 3 and tags is None and playbook == "site.yaml":
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

        # Pass host IP overrides matching the active provider
        extra_vars = [
            f"{settings.get_hostname(n)}_ansible_host={prov.get_node_ip(n)}"
            for n in (1, 2, 3)
        ]
        cmd.extend(["-e", " ".join(extra_vars)])

        # Setup SSH args for Ansible matching the active provider
        env = dict(os.environ)
        if cluster:
            c_path = Path(cluster)
            if c_path.exists():
                env["CABRITA_CLUSTER_MANIFEST"] = str(c_path.resolve())
        elif (Path.cwd() / "cluster.yaml").exists():
            env["CABRITA_CLUSTER_MANIFEST"] = str(
                (Path.cwd() / "cluster.yaml").resolve()
            )

        env["ANSIBLE_CONFIG"] = str(cfg_file)
        ssh_args = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
        if prov.paths.bastion_ssh_host:
            ssh_args += f" -J {prov.paths.bastion_ssh_host}"
        env["ANSIBLE_SSH_ARGS"] = ssh_args

    console.print(f"[cyan]Executing Ansible playbook '{playbook}'...[/cyan]")
    res = subprocess.run(cmd, env=env, check=False)
    if res.returncode != 0:
        console.print(
            f"[bold red]Playbook execution failed with exit code {res.returncode}.[/bold red]"
        )
        if exit_on_error:
            sys.exit(res.returncode)
        return False

    for n in available_nodes:
        update_node_state(settings, n, NodeLifecycle.READY)

    console.print(
        "[green]✓[/green] [bold green]Ansible configuration complete.[/bold green]"
    )
    return True


from typing import Annotated

import typer


def configure_cli(
    playbook: Annotated[
        str,
        typer.Option(
            "--playbook",
            "-p",
            help="Playbook file name inside ansible/playbooks/ (default: site.yaml)",
        ),
    ] = "site.yaml",
    node: Annotated[
        list[int] | None,
        typer.Option(
            "--node",
            "-n",
            help="Node ID(s) to configure (1, 2, or 3). Defaults to all nodes [1, 2, 3].",
        ),
    ] = None,
    limit: Annotated[
        str | None,
        typer.Option(
            "--limit",
            "-l",
            help="Limit execution to specific hosts/nodes (e.g. node1, node2)",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            "-c",
            help="Run in dry-run check mode without applying changes",
        ),
    ] = False,
    tags: Annotated[
        str | None,
        typer.Option(
            "--tags",
            "-t",
            help="Only execute tasks matching specified tags",
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-P",
            help="Node provider to configure (libvirt, bmc, or chameleon)",
        ),
    ] = None,
    cluster: Annotated[
        Path | None,
        typer.Option(
            "--cluster",
            help="Path to cluster manifest or cluster.yaml override file",
        ),
    ] = None,
) -> None:
    """Configure cluster nodes idempotently via Ansible."""
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

    configure_command(
        settings,
        node=node,
        playbook=playbook,
        limit=limit,
        check=check,
        tags=tags,
        provider=provider,
        cluster=manifest_file,
    )
