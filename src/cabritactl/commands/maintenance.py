"""Maintenance commands sharing the lifecycle's targets and locks."""

import json
import os
import subprocess
from pathlib import Path
from typing import Annotated, Literal

import typer

from cabritactl.bios import BiosProfile, get_profile_attributes
from cabritactl.commands.workflow import (
    ClusterOption,
    DryRunOption,
    JsonOption,
    NodeOption,
    YesOption,
    _error,
    service_context,
)


def power(
    action: Annotated[
        Literal["on", "off", "restart", "status", "metrics"], typer.Argument()
    ],
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Inspect or change power for declared nodes."""
    try:
        with service_context(cluster) as service:
            nodes = service.cluster.nodes(node)
            if action in ("status", "metrics"):
                provider = service.backend.provider
                results = [
                    {
                        "id": item.id,
                        "power": provider.get_power_status(item.id).value,
                        "metrics": provider.get_power_metrics(item.id)
                        if action == "metrics"
                        else None,
                    }
                    for item in nodes
                ]
                typer.echo(
                    json.dumps(results, indent=2)
                    if json_output
                    else "\n".join(str(result) for result in results)
                )
                return
            typer.echo(
                json.dumps({"action": action, "nodes": [item.id for item in nodes]})
            )
            if dry_run:
                return
            if not yes:
                typer.confirm(f"Power {action} for {len(nodes)} node(s)?", abort=True)
            keys = [
                service.cluster.lock_key(item, service.libvirt_uri) for item in nodes
            ]
            with service.locks.acquire(keys):
                for item in nodes:
                    match action:
                        case "on":
                            service.backend.start(item)
                        case "off":
                            service.backend.stop(item)
                        case "restart":
                            if not service.backend.provider.power_reset(item.id):
                                raise RuntimeError(
                                    f"Restart failed for {item.hostname}"
                                )
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def bios(
    action: Annotated[Literal["show", "apply", "backup"], typer.Argument()],
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    profile: Annotated[BiosProfile, typer.Option("--profile")] = BiosProfile.HPC,
    output: Annotated[Path, typer.Option("--output")] = Path("bios-backup.json"),
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Inspect, back up, or apply BIOS settings on Helvetios."""
    try:
        with service_context(cluster) as service:
            provider = service.backend.provider
            if provider.name != "helvetios":
                raise ValueError("BIOS operations require the Helvetios provider")
            nodes = service.cluster.nodes(node)
            if action == "apply":
                typer.echo(
                    json.dumps(
                        {"profile": profile.value, "nodes": [item.id for item in nodes]}
                    )
                )
                if dry_run:
                    return
                if not yes:
                    typer.confirm("Apply BIOS settings?", abort=True)
                with service.locks.acquire(
                    [service.cluster.lock_key(item) for item in nodes]
                ):
                    for item in nodes:
                        if not provider.set_bios_settings(
                            item.id, get_profile_attributes(profile)
                        ):
                            raise RuntimeError(
                                f"BIOS configuration failed for {item.hostname}"
                            )
                return
            results = {
                item.hostname: provider.get_bios_settings(item.id) for item in nodes
            }
            rendered = json.dumps(results, indent=2)
            if action == "backup" and not dry_run:
                with output.open("x") as stream:
                    stream.write(rendered)
            typer.echo(rendered)
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def locks(
    action: Annotated[Literal["list", "release"], typer.Argument()] = "list",
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    json_output: JsonOption = False,
) -> None:
    """Inspect resource locks; leases release automatically when their owner exits."""
    results = []
    try:
        with service_context(cluster) as service:
            for item in service.cluster.nodes(node):
                key = service.cluster.lock_key(item, service.libvirt_uri)
                try:
                    with service.locks.acquire([key]):
                        results.append({"id": item.id, "locked": False})
                except RuntimeError:
                    results.append({"id": item.id, "locked": True})
            typer.echo(json.dumps(results, indent=2))
            if action == "release" and any(entry["locked"] for entry in results):
                raise RuntimeError(
                    "Active locks cannot be broken; stop the owning operation to release its lease"
                )
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def ssh(
    command: Annotated[list[str] | None, typer.Argument()] = None,
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Open an interactive SSH session or run a command on selected nodes."""
    try:
        with service_context(cluster) as service:
            nodes = service.cluster.nodes(node)
            if command is None and len(nodes) != 1:
                raise ValueError("Interactive SSH requires exactly one --node")
            for item in nodes:
                manifest = service.cluster.manifest
                argv = [
                    "ssh",
                    "-o",
                    "ConnectTimeout=10",
                    "-i",
                    str(manifest.access.private_key),
                ]
                if service.backend.provider.name == "helvetios":
                    argv.extend(["-J", manifest.bastion.ssh_host])
                argv.append(f"{manifest.defaults.os.username}@{item.ip}")
                if command:
                    argv.extend(command)
                if dry_run:
                    typer.echo(json.dumps(argv))
                elif command:
                    subprocess.run(argv, check=True, timeout=manifest.access.timeout)
                else:
                    os.execvp("ssh", argv)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        _error(exc)
