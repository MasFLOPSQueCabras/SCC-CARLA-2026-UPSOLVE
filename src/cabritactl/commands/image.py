"""Capture managed, shut-down nodes and inspect immutable golden images."""

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from cabritactl.bootstrap.artifacts import artifact_lock
from cabritactl.bootstrap.golden import capture
from cabritactl.commands.workflow import ClusterOption, service_context
from cabritactl.core.image import inspect_image
from cabritactl.core.providers.base import PowerState
from cabritactl.paths import get_golden_image_dir

image_app = typer.Typer(name="image", no_args_is_help=True)


@image_app.command("capture")
def capture_command(
    node: Annotated[int, typer.Option("--node", "-n")],
    output: Annotated[Path, typer.Option("--output")],
    cluster: ClusterOption = Path("cluster.yaml"),
) -> None:
    """Capture a configured, shut-down VM into a new independent golden directory."""
    try:
        with service_context(cluster) as service:
            (selected,) = service.cluster.nodes([node])
            provider = service.backend.provider
            if provider.name != "libvirt" or selected.vm is None:
                raise ValueError("Capture requires a libvirt source node")
            with service.locks.acquire(
                [service.cluster.lock_key(selected, service.libvirt_uri)]
            ):
                checkpoint = service.state.read(node)
                if checkpoint.phase != "ready" or checkpoint.error is not None:
                    raise ValueError("Capture requires a configured, ready node")
                if provider.get_power_status(node) != PowerState.OFF:
                    raise ValueError(
                        "Shut down the node with cabritactl down before capture"
                    )
                storage = provider.paths.storage_dir
                if storage is None:
                    raise ValueError("Provider has no managed disk storage")
                source = storage / f"{service.cluster.resource_name(selected)}.qcow2"
                output = output.expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                with artifact_lock(output.with_suffix(".capture.lock")):
                    metadata = capture(
                        source,
                        output,
                        firmware=selected.vm.firmware,
                        username=service.cluster.manifest.defaults.os.username,
                        provisioning={
                            "cluster": service.cluster.identity,
                            "node": node,
                            "fingerprint": service.fingerprint,
                            "bootstrap": service.cluster.manifest.bootstrap.model_dump(
                                mode="json"
                            ),
                            "configuration": service.cluster.manifest.configuration.model_dump(
                                mode="json"
                            ),
                            "template_inputs": service.cluster.manifest.template_inputs,
                        },
                    )
                typer.echo(str(metadata))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@image_app.command("inspect")
def inspect_command(path: Annotated[Path, typer.Argument()]) -> None:
    """Print metadata for a disk or a golden capture manifest."""
    if path.suffix == ".json":
        from cabritactl.bootstrap.golden import GoldenMetadata

        typer.echo(
            GoldenMetadata.model_validate_json(path.read_text()).model_dump_json(
                indent=2
            )
        )
    else:
        typer.echo(json.dumps(asdict(inspect_image(path)), default=str, indent=2))


@image_app.command("list")
def list_images() -> None:
    """List captures in Cabrita's golden cache directory."""
    for path in sorted(get_golden_image_dir().glob("*/golden.json")):
        typer.echo(str(path))
