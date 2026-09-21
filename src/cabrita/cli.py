from typing import Annotated

import typer
from rich.console import Console

from cabrita import __version__
from cabrita.commands.bios import bios_app
from cabrita.commands.cluster import cluster_app
from cabrita.commands.cluster import init as cluster_init
from cabrita.commands.configure import configure_cli
from cabrita.commands.deploy import deploy_cli
from cabrita.commands.down import down_cli
from cabrita.commands.image import image_app
from cabrita.commands.lock import lock_app
from cabrita.commands.plan import plan_cli
from cabrita.commands.power import power_app
from cabrita.commands.provider import provider_app
from cabrita.commands.ssh import ssh_cli
from cabrita.commands.status import status_cli
from cabrita.commands.up import up_cli

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
app.add_typer(power_app, name="power")
app.add_typer(bios_app, name="bios")
app.add_typer(lock_app, name="lock")

# Register root commands
app.command(
    "init",
    help="Initialize a cluster workspace with values.yaml and provider templates",
)(cluster_init)
app.command(
    "plan",
    help="Compute execution plan comparing declared manifest against live state",
)(plan_cli)
app.command("up", help="Provision cluster node OS and run configuration in parallel")(
    up_cli
)
app.command(
    "deploy",
    help="Deploy cluster node OS image and wait for SSH without running Ansible",
)(deploy_cli)
app.command("provision", help="Alias for deploy")(deploy_cli)
app.command("down", help="Tear down and decommission cluster node(s)")(down_cli)
app.command(
    "status",
    help="Inspect current cluster state, node lifecycles, and operational locks",
)(status_cli)
app.command("configure", help="Configure cluster nodes idempotently via Ansible")(
    configure_cli
)
app.command("ssh", help="Connect to a cluster node or the bastion via native SSH")(
    ssh_cli
)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
