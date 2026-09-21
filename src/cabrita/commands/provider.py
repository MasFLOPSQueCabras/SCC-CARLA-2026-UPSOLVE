import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cabrita.core.di import ProviderNotInstalledError, create_registry
from cabrita.core.manifest import parse_manifest

console = Console()

provider_app = typer.Typer(
    name="provider",
    help="Manage modular cluster providers, presets, and templates",
    no_args_is_help=True,
)


def customize_helvetios_yaml(
    yaml_text: str,
    team_id: int | None = None,
    username: str | None = None,
    bastion_ssh_host: str | None = None,
) -> str:
    """Customizes Helvetios preset YAML with team ID, user, and bastion overrides."""
    if team_id is not None:
        old_team = 72
        yaml_text = yaml_text.replace(f"10.2.{old_team}.", f"10.2.{team_id}.")
        yaml_text = yaml_text.replace(f"10.1.{old_team}.", f"10.1.{team_id}.")
        yaml_text = yaml_text.replace(f"80{old_team}", f"{8000 + team_id}")
        old_user = f"scct-26{old_team}"
        new_user = username or f"scct-26{team_id:02d}"
        yaml_text = yaml_text.replace(old_user, new_user)
    elif username is not None:
        yaml_text = yaml_text.replace("scct-2672", username)

    if bastion_ssh_host is not None:
        yaml_text = yaml_text.replace(
            "ssh_host: cabrita-bastion", f"ssh_host: {bastion_ssh_host}"
        )

    return yaml_text


def scaffold_provider(
    provider: str,
    profile: str | None = None,
    team_id: int | None = None,
    username: str | None = None,
    bastion: str | None = None,
    target_dir: Path = Path("."),
    force: bool = False,
) -> None:
    """Configures a provider preset into cluster.yaml and stages templates."""
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    canon = (
        "helvetios"
        if provider.lower() in ("helvetios", "bmc")
        else ("libvirt" if provider.lower() in ("libvirt", "vm") else provider.lower())
    )

    registry = create_registry()
    try:
        cls = registry.get_provider_class(canon)
    except ProviderNotInstalledError as e:
        console.print(f"[bold red]Provider error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except ValueError:
        valid = ", ".join(registry.list_providers())
        console.print(
            f"[bold red]Unknown provider '{provider}'. Available providers: {valid}[/bold red]"
        )
        raise typer.Exit(code=1)

    presets = cls.list_presets()
    selected_profile = profile or presets[0]

    try:
        yaml_str = cls.get_preset_config(selected_profile)
    except Exception as e:  # noqa: BLE001
        console.print(
            f"[bold red]Failed to load preset '{selected_profile}' for provider '{canon}': {e}[/bold red]"
        )
        raise typer.Exit(code=1)

    if canon == "helvetios":
        yaml_str = customize_helvetios_yaml(
            yaml_str,
            team_id=team_id,
            username=username,
            bastion_ssh_host=bastion,
        )

    # Validate before writing
    try:
        parse_manifest(yaml_str)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Generated manifest validation failed: {e}[/bold red]")
        raise typer.Exit(code=1)

    values_path = target_dir / "cluster.yaml"
    if values_path.exists() and not force:
        console.print(
            f"[yellow]cluster.yaml already exists at {values_path}, keeping existing. (Use --force to overwrite)[/yellow]"
        )
    else:
        values_path.write_text(yaml_str, encoding="utf-8")
        console.print(
            f"[green]✓[/green] Created [bold]{values_path}[/bold] (provider: {canon}, profile: {selected_profile})"
        )

    # Stage templates
    templates_dir = target_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    tpl_source = cls.get_templates_dir()
    if tpl_source and tpl_source.is_dir():
        for item in tpl_source.rglob("*.j2"):
            rel = item.relative_to(tpl_source)
            dest = templates_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or force:
                shutil.copyfile(item, dest)
                console.print(f"  [cyan]+[/cyan] Staged template: templates/{rel}")
    else:
        console.print(
            f"  [dim]No bundled templates needed or found for provider '{canon}'.[/dim]"
        )

    # Provider preflight warnings
    if canon == "libvirt":
        if not shutil.which("qemu-img"):
            console.print(
                "  [yellow]⚠ 'qemu-img' not found on PATH. Install qemu-img or qemu-utils.[/yellow]"
            )
        if not shutil.which("virsh"):
            console.print(
                "  [yellow]⚠ 'virsh' not found on PATH. Install libvirt-client.[/yellow]"
            )


@provider_app.command("list")
def list_providers_cli() -> None:
    """List available modular cluster providers and their preset configurations."""
    table = Table(
        title="Registered Modular Cluster Providers", header_style="bold cyan"
    )
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Available Presets")

    registry = create_registry()
    for name in registry.list_providers():
        try:
            cls = registry.get_provider_class(name)
            presets = cls.list_presets()
            table.add_row(
                name, "[bold green]installed[/bold green]", ", ".join(presets)
            )
        except ProviderNotInstalledError:
            table.add_row(name, "[dim yellow]missing dependencies[/dim yellow]", "-")
        except Exception as e:  # noqa: BLE001
            table.add_row(name, f"[bold red]error: {e}[/bold red]", "-")

    console.print(table)


@provider_app.command("add")
def add_provider_cli(
    provider: Annotated[
        str,
        typer.Argument(
            help="Provider to add to the workspace (e.g. helvetios or libvirt/vm)"
        ),
    ],
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            "-p",
            help="Config profile (e.g. 'standard' or 'hw-optimized' for libvirt, 'hpc' for helvetios)",
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
            help="Bastion SSH host (e.g. bastion.helvetios.epfl.ch)",
        ),
    ] = None,
    target_dir: Annotated[
        Path,
        typer.Option(
            "--dir",
            "-d",
            help="Target directory to configure",
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite existing cluster.yaml or templates",
        ),
    ] = False,
) -> None:
    """Add a provider's configuration and templates to the current workspace."""
    scaffold_provider(
        provider=provider,
        profile=profile,
        team_id=team_id,
        username=username,
        bastion=bastion,
        target_dir=target_dir,
        force=force,
    )
    console.print(
        f"\n[bold green]Provider '{provider}' added successfully![/bold green]\n"
        f"Workspace is ready in [bold]{target_dir.resolve()}[/bold].\n"
        f"You can now run [bold]cabrita cluster show[/bold], [bold]cabrita plan[/bold], or [bold]cabrita up[/bold]."
    )
