import contextlib
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from scc_core.manifest import load_manifest

console = Console()

cluster_app = typer.Typer(
    name="cluster",
    help="Author, initialize, validate, and inspect cluster definitions",
    no_args_is_help=True,
)


def _get_preset_config(provider: str, profile: str) -> Path:
    repo_configs = Path(__file__).parents[3] / "configs" / "clusters"
    match (provider.lower(), profile.lower()):
        case ("vm" | "libvirt", "hw-optimized" | "cabrita"):
            target = repo_configs / "vm-hw-optimized.yaml"
        case ("vm" | "libvirt", _):
            target = repo_configs / "vm-standard.yaml"
        case ("helvetios" | "bmc", _):
            target = repo_configs / "helvetios-hpc.yaml"
        case _:
            target = repo_configs / "vm-standard.yaml"
    return target


@cluster_app.command("init")
def init(
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            "-P",
            help="Target provider: 'vm' (libvirt) or 'helvetios' (baremetal HPC)",
        ),
    ] = "vm",
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            "-p",
            help="Config profile: 'standard' or 'hw-optimized' (for host hardware / cabrita)",
        ),
    ] = "standard",
    target_dir: Annotated[
        Path,
        typer.Option(
            "--dir",
            "-d",
            help="Target directory to initialize cluster files in",
        ),
    ] = Path("."),
) -> None:
    """Initialize a cluster workspace with values.yaml and provider templates for customizing."""
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    values_path = target_dir / "values.yaml"
    templates_dir = target_dir / "templates"

    source_config = _get_preset_config(provider, profile)
    if not source_config.exists():
        console.print(f"[bold red]Preset config not found: {source_config}[/bold red]")
        raise typer.Exit(code=1)

    # 1. Copy values.yaml
    if values_path.exists():
        console.print(
            f"[yellow]values.yaml already exists at {values_path}, keeping existing.[/yellow]"
        )
    else:
        shutil.copyfile(source_config, values_path)
        console.print(
            f"[green]✓[/green] Created [bold]{values_path}[/bold] (profile: {provider}/{profile})"
        )

    # 2. Copy templates
    templates_dir.mkdir(parents=True, exist_ok=True)
    if provider.lower() in ("vm", "libvirt"):
        pkg_templates = (
            Path(__file__).parents[3]
            / "packages"
            / "scc-provider-libvirt"
            / "src"
            / "scc_provider_libvirt"
            / "templates"
        )
    else:
        pkg_templates = (
            Path(__file__).parents[3]
            / "packages"
            / "scc-provider-helvetios"
            / "src"
            / "scc_provider_helvetios"
            / "templates"
        )

    if pkg_templates.exists():
        for item in pkg_templates.rglob("*.j2"):
            rel = item.relative_to(pkg_templates)
            dest = templates_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copyfile(item, dest)
                console.print(f"  [cyan]+[/cyan] Staged template: templates/{rel}")

    console.print(
        f"\n[bold green]Cluster workspace initialized successfully in {target_dir}![/bold green]\n"
        f"Customize your parameters in [bold]values.yaml[/bold] and templates in [bold]templates/[/bold]."
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
            help="Path to cluster manifest (defaults to values.yaml or active config)",
        ),
    ] = Path("values.yaml"),
) -> None:
    """Pretty-print declared cluster topology, network parameters, and node sizing."""
    path = manifest_path.expanduser().resolve()
    if not path.exists():
        # Fallback to configs/clusters/vm-standard.yaml
        fallback = (
            Path(__file__).parents[3] / "configs" / "clusters" / "vm-standard.yaml"
        )
        if fallback.exists():
            path = fallback
        else:
            console.print(f"[bold red]No cluster manifest found at {path}[/bold red]")
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
    """List available pre-packaged cluster configs and local workspaces."""
    configs_dir = Path(__file__).parents[3] / "configs" / "clusters"
    table = Table(title="Available Cluster Configurations", header_style="bold cyan")
    table.add_column("Profile / Config File")
    table.add_column("Provider")
    table.add_column("Description")

    if configs_dir.exists():
        for p in sorted(configs_dir.glob("*.yaml")):
            try:
                m = load_manifest(p)
                table.add_row(p.name, m.provider, m.description)
            except Exception:  # noqa: BLE001
                table.add_row(p.name, "unknown", "-")

    # Local workspace values.yaml
    local_val = Path.cwd() / "values.yaml"
    if local_val.exists():
        with contextlib.suppress(Exception):
            m = load_manifest(local_val)
            table.add_row("./values.yaml (active)", m.provider, m.description)

    console.print(table)
