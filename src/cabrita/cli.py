from typing import Annotated

import typer
from rich.console import Console

from cabrita import __version__
from cabrita.commands import maintenance, workflow
from cabrita.commands.cluster import cluster_app
from cabrita.commands.image import image_app
from cabrita.commands.provider import provider_app

console = Console()

app = typer.Typer(
    name="cabrita",
    help="Authorable clusters on libvirt and Helvetios",
    no_args_is_help=True,
)


def _version(value: bool) -> None:
    if value:
        print(__version__)
        raise typer.Exit()


@app.callback()
def options(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Show installed version"
        ),
    ] = False,
) -> None:
    pass


# Mount modular sub-apps
app.add_typer(cluster_app, name="cluster")
app.add_typer(provider_app, name="provider")
app.add_typer(image_app, name="image")

# Register root commands
app.command(
    "init",
    help="Initialize a cluster workspace with cluster.yaml and provider templates",
)(workflow.init)
for command in (
    workflow.validate,
    workflow.doctor,
    workflow.plan,
    workflow.up,
    workflow.deploy,
    workflow.configure,
    workflow.down,
    workflow.destroy,
    workflow.status,
    workflow.verify,
):
    app.command()(command)
app.command("power")(maintenance.power)
app.command("bios")(maintenance.bios)
app.command("lock")(maintenance.locks)
app.command("ssh")(maintenance.ssh)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
