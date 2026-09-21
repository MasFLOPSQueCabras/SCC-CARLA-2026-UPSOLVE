from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cabrita.config import get_settings
from cabrita.db import break_lock, get_active_locks

console = Console()

lock_app = typer.Typer(
    name="lock",
    help="Inspect and manage cluster operational locks",
    no_args_is_help=True,
)


@lock_app.command("list")
def lock_list() -> None:
    """List all active and expired operational cluster locks."""
    settings = get_settings()
    locks = get_active_locks(settings)
    if not locks:
        console.print("[dim]No operational locks are currently held.[/dim]")
        return

    table = Table(
        title="Shared Operational Locks",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Resource", justify="center", style="bold")
    table.add_column("Holder", justify="center")
    table.add_column("Operation", justify="center")
    table.add_column("Acquired At", justify="center")
    table.add_column("Elapsed", justify="center")
    table.add_column("Timeout", justify="center")
    table.add_column("Status", justify="center")

    for lk in locks:
        elapsed_m = lk.elapsed_sec // 60
        elapsed_s = lk.elapsed_sec % 60
        elapsed_str = f"{elapsed_m}m {elapsed_s}s"
        status_str = (
            "[bold red]EXPIRED[/bold red]"
            if lk.is_expired
            else "[bold yellow]LOCKED[/bold yellow]"
        )
        table.add_row(
            lk.resource,
            lk.holder,
            lk.operation,
            lk.acquired_at,
            elapsed_str,
            f"{lk.timeout_sec}s",
            status_str,
        )

    console.print(table)


@lock_app.command("release")
def lock_release(
    resource: Annotated[
        str,
        typer.Argument(
            help="Resource lock to release/break ('node-1', 'node-2', 'node-3', 'cluster', or 'all')"
        ),
    ],
) -> None:
    """Release or break an operational lock on a resource or all resources."""
    settings = get_settings()
    break_lock(settings, resource)
    console.print(f"[bold green]✓ Released lock on '{resource}'.[/bold green]")
