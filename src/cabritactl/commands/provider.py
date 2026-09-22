import typer
from rich.console import Console
from rich.table import Table

from cabritactl.core.di import ProviderNotInstalledError, create_registry

console = Console()
provider_app = typer.Typer(
    name="provider", help="Inspect available providers", no_args_is_help=True
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
