from pathlib import Path
from typing import Annotated

import typer

from scc_carla.commands.down import down_command
from scc_carla.commands.status import status_command
from scc_carla.commands.up import up_command
from scc_carla.config import get_settings

app = typer.Typer(
    name="scc",
    help="SCC@CARLA Cluster Management CLI",
    no_args_is_help=True,
)


@app.command("up")
def up(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to provision (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Provision all nodes (1, 2, and 3)"),
    ] = False,
    pubkey: Annotated[
        Path | None, typer.Option("--pubkey", "-k", help="Path to SSH public key")
    ] = None,
) -> None:
    settings = get_settings()
    up_command(settings, node=node, all_nodes=all_nodes, pubkey_path=pubkey)


@app.command("down")
def down(
    node: Annotated[
        int | None,
        typer.Option("--node", "-n", help="Node ID to decommission (1, 2, or 3)"),
    ] = None,
    all_nodes: Annotated[
        bool,
        typer.Option("--all", "-a", help="Decommission all nodes (1, 2, and 3)"),
    ] = False,
    reset_db: Annotated[
        bool,
        typer.Option("--reset-db", "-r", help="Reset node states in database"),
    ] = False,
) -> None:
    settings = get_settings()
    down_command(settings, node=node, all_nodes=all_nodes, reset_db=reset_db)


@app.command("status")
def status() -> None:
    settings = get_settings()
    status_command(settings)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
