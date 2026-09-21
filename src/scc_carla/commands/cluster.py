import contextlib
import importlib.resources as ir
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from scc_core.di import container
from scc_core.manifest import load_manifest, parse_manifest

from scc_carla.commands.provider import scaffold_provider

console = Console()

cluster_app = typer.Typer(
    name="cluster",
    help="Author, initialize, validate, and inspect cluster definitions",
    no_args_is_help=True,
)


@cluster_app.command("init")
def init(
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            "-P",
            help="Target provider: 'libvirt' (vm) or 'helvetios' (baremetal HPC)",
        ),
    ] = None,
    no_provider: Annotated[
        bool,
        typer.Option(
            "--no-provider",
            help="Initialize base workspace (Ansible recipes) only, without adding a provider",
        ),
    ] = False,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            "-p",
            help="Config profile: e.g. 'standard' or 'hw-optimized' (libvirt), 'hpc' (helvetios)",
        ),
    ] = None,
    team_id: Annotated[
        int | None,
        typer.Option(
            "--team-id",
            help="Team ID for IP/subnet/port configuration (Helvetios HPC)",
        ),
    ] = None,
    username: Annotated[
        str | None,
        typer.Option(
            "--username",
            "-u",
            help="Cluster node / bastion SSH username",
        ),
    ] = None,
    bastion: Annotated[
        str | None,
        typer.Option(
            "--bastion",
            "-b",
            help="Bastion SSH host (Helvetios HPC)",
        ),
    ] = None,
    target_dir: Annotated[
        Path,
        typer.Argument(
            help="Target directory to initialize cluster files in (defaults to current directory)",
        ),
    ] = Path("."),
    install_deps: Annotated[
        bool,
        typer.Option(
            "--install-deps/--no-install-deps",
            help="Install Python dependencies for the selected provider via uv sync (if pyproject.toml is present)",
        ),
    ] = True,
) -> None:
    """Initialize a cluster workspace with Ansible recipes and modular provider assets."""
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Base workspace scaffolding: copy global Ansible recipes
    ansible_dest = target_dir / "ansible"
    try:
        ref = ir.files("scc_carla").joinpath("ansible")
        with ir.as_file(ref) as p:
            shutil.copytree(p, ansible_dest, dirs_exist_ok=True)
        console.print(
            f"[green]✓[/green] Scaffolding Ansible recipes into [bold]{ansible_dest}[/bold]"
        )
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Failed to scaffold Ansible recipes: {e}[/bold red]")
        raise typer.Exit(code=1)

    # 2. Check if user wants base workspace only
    if no_provider:
        console.print(
            f"\n[bold green]Base workspace initialized successfully in {target_dir}![/bold green]\n"
            "Ansible recipes are staged in [bold]ansible/[/bold].\n"
            "Run [bold]scc provider add <helvetios|libvirt>[/bold] when ready to configure a provider."
        )
        return

    # 3. Provider selection and configuration
    chosen_prov = provider
    if chosen_prov is None:
        if sys.stdin.isatty():
            console.print(
                "\n[bold cyan]Select a cluster provider to configure:[/bold cyan]"
            )
            console.print("  [bold]1[/bold]) Helvetios HPC (HPE iLO BMC baremetal)")
            console.print(
                "  [bold]2[/bold]) Libvirt VM (Local QEMU/KVM virtual machines)"
            )
            console.print(
                "  [bold]3[/bold]) Base workspace only (recipes without provider)"
            )
            choice = typer.prompt("Enter choice [1/2/3]", default="1")
            if choice in ("1", "helvetios", "bmc"):
                chosen_prov = "helvetios"
                team_id = typer.prompt("Team ID", default=team_id or 72, type=int)
                default_user = username or f"scct-26{team_id:02d}"
                username = typer.prompt("Cluster username", default=default_user)
                bastion = typer.prompt(
                    "Bastion SSH host", default=bastion or "bastion.helvetios.epfl.ch"
                )
                profile = profile or "hpc"
            elif choice in ("2", "vm", "libvirt"):
                chosen_prov = "libvirt"
                profile = typer.prompt(
                    "Profile (standard / hw-optimized)", default=profile or "standard"
                )
            else:
                console.print(
                    f"\n[bold green]Base workspace initialized successfully in {target_dir}![/bold green]\n"
                    "Run [bold]scc provider add <helvetios|libvirt>[/bold] when ready."
                )
                return
        else:
            chosen_prov = "vm"
            profile = profile or "standard"

    canon_prov = (
        "helvetios" if chosen_prov.lower() in ("helvetios", "bmc") else "libvirt"
    )

    # 4. Install provider dependencies if in a uv project workspace
    if install_deps and (target_dir / "pyproject.toml").exists() and shutil.which("uv"):
        console.print(
            f"[cyan]Installing Python dependencies for provider [bold]{canon_prov}[/bold] via uv sync...[/cyan]"
        )
        try:
            subprocess.run(["uv", "sync", "--extra", canon_prov], check=True)
            console.print(
                f"[green]✓[/green] Dependencies for [bold]{canon_prov}[/bold] installed successfully."
            )
        except subprocess.CalledProcessError as e:
            console.print(
                f"[yellow]⚠ Failed to install dependencies via uv sync: {e}[/yellow]"
            )

    # 5. Scaffold provider preset and templates
    scaffold_provider(
        provider=chosen_prov,
        profile=profile,
        team_id=team_id,
        username=username,
        bastion=bastion,
        target_dir=target_dir,
    )

    console.print(
        f"\n[bold green]Cluster workspace initialized successfully in {target_dir}![/bold green]\n"
        f"Provider: [bold]{canon_prov}[/bold] | Profile: [bold]{profile or 'default'}[/bold]\n"
        f"Customize parameters in [bold]values.yaml[/bold], templates in [bold]templates/[/bold], and recipes in [bold]ansible/[/bold]."
    )


@cluster_app.command("validate")
def validate(
    manifest_path: Annotated[
        Path,
        typer.Argument(help="Path to cluster manifest or values.yaml to validate"),
    ] = Path("values.yaml"),
) -> None:
    """Validate cluster manifest schema, IP formatting, and resource allocation."""
    path = manifest_path.expanduser().resolve()
    if not path.exists():
        console.print(f"[bold red]Manifest file not found: {path}[/bold red]")
        raise typer.Exit(code=1)

    try:
        manifest = load_manifest(path)
        console.print("[bold green]✓ Manifest is valid![/bold green]")
        console.print(f"  Name: [cyan]{manifest.name}[/cyan]")
        console.print(f"  Provider: [cyan]{manifest.provider}[/cyan]")
        console.print(f"  Nodes: [cyan]{len(manifest.nodes)} declared[/cyan]")
        console.print(f"  Subnet: [cyan]{manifest.network.subnet}[/cyan]")
        console.print(f"  Gateway: [cyan]{manifest.network.gateway}[/cyan]")
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Validation Error: {e}[/bold red]")
        raise typer.Exit(code=1)


@cluster_app.command("show")
def show(
    manifest_path: Annotated[
        Path,
        typer.Option(
            "--manifest",
            "-m",
            help="Path to cluster manifest (defaults to values.yaml)",
        ),
    ] = Path("values.yaml"),
) -> None:
    """Pretty-print declared cluster topology, network parameters, and node sizing."""
    path = manifest_path.expanduser().resolve()
    if not path.exists():
        console.print(
            f"[bold red]No cluster manifest found at {path}.[/bold red]\n"
            "[dim]Run 'scc init' or 'scc provider add <provider>' to initialize a workspace.[/dim]"
        )
        raise typer.Exit(code=1)

    manifest = load_manifest(path)
    console.print(
        f"[bold cyan]Cluster:[/bold cyan] {manifest.name} ({manifest.provider})"
    )
    if manifest.description:
        console.print(f"[italic]{manifest.description}[/italic]\n")

    table = Table(title="Declared Nodes", header_style="bold magenta")
    table.add_column("Node ID", justify="center")
    table.add_column("Hostname")
    table.add_column("Role")
    table.add_column("IP Address")
    table.add_column("MAC Address")
    table.add_column("Hardware / Sizing")

    for n in manifest.nodes:
        if n.vm:
            sizing = f"{n.vm.vcpus} vCPU, {n.vm.memory_mb}MB RAM, {n.vm.disk.size_gb}GB ({n.vm.disk.bus}/{n.vm.cpu_mode})"
        elif n.hardware:
            sizing = (
                f"Profile: {n.hardware.bios_profile}, Target: {n.hardware.target_disk}"
            )
        else:
            sizing = "Defaults"

        table.add_row(
            str(n.id),
            n.hostname,
            n.role,
            n.ip,
            n.mac,
            sizing,
        )
    console.print(table)


@cluster_app.command("list")
def list_clusters() -> None:
    """List available pre-packaged cluster presets and local workspaces."""
    table = Table(title="Available Cluster Configurations", header_style="bold cyan")
    table.add_column("Profile / Preset")
    table.add_column("Provider")
    table.add_column("Description")

    # Inspect registered providers and presets
    for prov_name in container.providers.list_providers():
        try:
            cls = container.providers.get_provider_class(prov_name)
            for preset in cls.list_presets():
                try:
                    yaml_str = cls.get_preset_config(preset)
                    m = parse_manifest(yaml_str)
                    table.add_row(
                        f"{prov_name}/{preset}", prov_name, m.description or "-"
                    )
                except Exception:  # noqa: BLE001
                    table.add_row(f"{prov_name}/{preset}", prov_name, "-")
        except Exception:  # noqa: BLE001, S110
            pass

    # Local workspace values.yaml
    local_val = Path.cwd() / "values.yaml"
    if local_val.exists():
        with contextlib.suppress(Exception):
            m = load_manifest(local_val)
            table.add_row("./values.yaml (active)", m.provider, m.description)

    console.print(table)


from scc_carla.commands.plan import plan_cli

cluster_app.command(
    "plan",
    help="Compute execution plan comparing declared manifest against live state",
)(plan_cli)
