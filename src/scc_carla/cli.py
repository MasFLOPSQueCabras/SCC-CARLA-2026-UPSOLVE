import typer
from rich.console import Console

from scc_carla.commands.bios import bios_app
from scc_carla.commands.cluster import cluster_app
from scc_carla.commands.cluster import init as cluster_init
from scc_carla.commands.configure import configure_cli
from scc_carla.commands.deploy import deploy_cli
from scc_carla.commands.down import down_cli
from scc_carla.commands.lock import lock_app
from scc_carla.commands.plan import plan_cli
from scc_carla.commands.power import power_app
from scc_carla.commands.ssh import ssh_cli
from scc_carla.commands.status import status_cli
from scc_carla.commands.up import up_cli

console = Console()

app = typer.Typer(
    name="scc",
    help="SCC@CARLA Cluster Management CLI",
    no_args_is_help=True,
)

# Mount modular sub-apps
app.add_typer(cluster_app, name="cluster")
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
    help="Show cluster execution plan, resource drift, and operations diff",
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
