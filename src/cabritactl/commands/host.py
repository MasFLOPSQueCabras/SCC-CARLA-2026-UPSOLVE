"""Host setup deliberately avoids importing libvirt's optional Python binding."""

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from cabritactl.core.resolved import ResolvedCluster
from cabritactl.host.firewall import apply_rules, cleanup_rules, rules_for
from cabritactl.host.setup import (
    active_firewall,
    authorization_step,
    daemon_steps,
    distro,
    package_steps,
    run_steps,
    save_plan,
    shell_plan,
    storage_steps,
)
from cabritactl.paths import get_state_dir

host_app = typer.Typer(name="host", no_args_is_help=True)


@host_app.command()
def setup(
    apply: Annotated[
        bool, typer.Option("--apply", help="Apply the displayed host preparation steps")
    ] = False,
    full: Annotated[
        bool, typer.Option("--full", help="Include OEMDRV and golden-image tools")
    ] = False,
    cluster: Annotated[Path | None, typer.Option("--cluster")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Preview or apply Fedora 44 / Ubuntu 26.04 prerequisites and cluster rules."""
    try:
        system = distro()
        steps = (
            package_steps(system, full)
            + daemon_steps(system)
            + [authorization_step()]
            + storage_steps()
        )
        if cluster is not None:
            resolved = ResolvedCluster.load(cluster)
            if apply and os.geteuid() != 0:
                subprocess.run(["sudo", "-v"], check=True)
            manager = active_firewall()
            rules = rules_for(resolved, manager)
            typer.echo(
                json.dumps([r.add for r in rules])
                if json_output
                else "\n".join(shlex.join(r.add) for r in rules)
            )
            if apply:
                apply_rules(resolved)
            return
        typer.echo(save_plan(steps) if json_output else shell_plan(steps))
        if apply:
            log = get_state_dir() / "host-setup.log"
            run_steps(package_steps(system, full), log)
            run_steps(
                daemon_steps(system) + [authorization_step()] + storage_steps(), log
            )
            typer.echo(
                f"Host setup completed. Log: {log}. Start a new login session for group membership, then install the libvirt extra and run doctor."
            )
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@host_app.command()
def cleanup(
    cluster: Annotated[Path, typer.Option("--cluster")],
    apply: Annotated[bool, typer.Option("--apply")] = False,
) -> None:
    """Preview/remove only recorded Cabrita firewall rules after cluster destruction."""
    try:
        commands = cleanup_rules(ResolvedCluster.load(cluster), apply)
        typer.echo("\n".join(shlex.join(argv) for argv in commands) or "No owned rules")
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
