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
def up() -> None:
    settings = get_settings()
    up_command(settings)


@app.command("down")
def down(
    reset_db: bool = typer.Option(
        False, "--reset-db", "-r", help="Reset node states in database"
    ),
) -> None:
    settings = get_settings()
    down_command(settings, reset_db=reset_db)


@app.command("status")
def status() -> None:
    settings = get_settings()
    status_command(settings)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
