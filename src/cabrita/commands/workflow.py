"""Consistent lifecycle commands operating on a single resolved manifest."""

import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer
import yaml

from cabrita.config import ClusterSettings
from cabrita.core.lifecycle.remote_locks import BastionLocks
from cabrita.core.lifecycle.service import (
    LifecycleService,
    Operation,
    ResourceLocks,
    StateStore,
)
from cabrita.core.resolved import ResolvedCluster
from cabrita.lifecycle import ProviderBackend
from cabrita.paths import get_cache_dir, get_state_dir
from cabrita.providers.factory import get_provider

ClusterOption = Annotated[Path, typer.Option("--cluster", "-c")]
NodeOption = Annotated[list[int] | None, typer.Option("--node", "-n")]
YesOption = Annotated[bool, typer.Option("--yes", "-y")]
DryRunOption = Annotated[bool, typer.Option("--dry-run")]
JsonOption = Annotated[bool, typer.Option("--json")]
ReinstallOption = Annotated[
    bool, typer.Option("--reinstall", help="Explicitly replace installed systems")
]


@contextmanager
def service_context(path: Path) -> Iterator[LifecycleService[ProviderBackend]]:
    cluster = ResolvedCluster.load(path)
    manifest = cluster.manifest
    settings = ClusterSettings(
        manifest=manifest,
        provider=manifest.provider,
        node_username=manifest.defaults.os.username,
        bastion_ssh_host=manifest.bastion.ssh_host,
        bastion_http_ip=manifest.bastion.http_bind_ip,
        bastion_http_port=manifest.bastion.http_port,
        bastion_serve_dir=manifest.bastion.remote_serve_dir,
        gateway_ip=manifest.network.gateway,
        dns_ip=manifest.network.dns,
    )
    state = StateStore(cluster.state_directory(get_state_dir()))
    with get_provider(settings) as provider:
        backend = ProviderBackend(
            cluster,
            provider,
            state,
            get_cache_dir() / "clusters" / cluster.identity,
            manifest.access.public_key,
            manifest.access.private_key,
            manifest.access.timeout,
        )
        yield LifecycleService(
            cluster,
            backend,
            state,
            ResourceLocks(get_state_dir() / "locks")
            if manifest.provider == "libvirt"
            else BastionLocks(manifest.bastion.ssh_host),
            max_workers=manifest.access.max_workers,
            libvirt_uri=settings.libvirt_uri,
        )


def _error(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1) from exc


def init(
    directory: Annotated[Path, typer.Argument()] = Path("."),
    provider: Annotated[str, typer.Option("--provider")] = "libvirt",
    artifact: Annotated[
        Path | None,
        typer.Option(
            "--artifact", help="Local base image or installer ISO; checksum is computed"
        ),
    ] = None,
) -> None:
    """Create a cluster manifest and editable templates from packaged resources."""
    packages = {
        "libvirt": ("cabrita.providers.libvirt_backend", "vm-standard.yaml"),
        "helvetios": ("cabrita.providers.helvetios", "helvetios-hpc.yaml"),
    }
    if provider not in packages:
        raise typer.BadParameter("Provider must be libvirt or helvetios")
    package, filename = packages[provider]
    directory = directory.resolve()
    destination = directory / "cluster.yaml"
    if destination.exists():
        raise typer.BadParameter(f"Manifest already exists: {destination}")
    document = yaml.safe_load(files(package).joinpath("configs", filename).read_text())
    method = "cloud-init" if provider == "libvirt" else "embedded-kickstart"
    document["bootstrap"] = {
        "method": method,
        "artifact": "os",
        "templates": "templates",
        "build_on": "local" if provider == "libvirt" else "bastion",
    }
    if artifact is None:
        source, checksum = "REPLACE_WITH_ARTIFACT_PATH_OR_URL", "REPLACE_WITH_SHA256"
    else:
        artifact = artifact.expanduser().resolve()
        with artifact.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        source = str(artifact)
    document["artifacts"] = {
        "os": {
            "source": source,
            "sha256": checksum,
            "format": "qcow2" if provider == "libvirt" else "iso",
        }
    }
    document["configuration"] = {
        "profile": "none" if provider == "libvirt" else "scc-carla-2026"
    }
    directory.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(document, sort_keys=False))
    shutil.copytree(
        str(files(package).joinpath("templates")),
        directory / "templates",
        dirs_exist_ok=True,
    )
    typer.echo(f"Created {destination}")
    if artifact is None:
        typer.echo("Set artifacts.os.source and artifacts.os.sha256 before validation.")


def validate(cluster: ClusterOption = Path("cluster.yaml")) -> None:
    """Validate authoring inputs and provider/media capabilities."""
    try:
        resolved = ResolvedCluster.load(cluster)
        typer.echo(f"Valid: {resolved.identity} ({len(resolved.nodes())} nodes)")
    except (ValueError, OSError) as exc:
        _error(exc)


def doctor(
    cluster: ClusterOption = Path("cluster.yaml"), json_output: JsonOption = False
) -> None:
    """Check local prerequisites and provider access before execution."""
    try:
        resolved = ResolvedCluster.load(cluster)
        required = ["ssh", "ansible-playbook"]
        if resolved.manifest.provider == "libvirt":
            required.extend(["qemu-img", "virsh", "mkfs.vfat", "mcopy"])
        problems = [
            f"Missing executable: {tool}"
            for tool in required
            if shutil.which(tool) is None
        ]
        for path in (
            resolved.manifest.access.public_key,
            resolved.manifest.access.private_key,
        ):
            if not path.is_file():
                problems.append(f"Missing SSH key: {path}")
        with service_context(cluster) as service:
            service.plan()
        typer.echo(
            json.dumps({"ok": not problems, "problems": problems})
            if json_output
            else "\n".join(problems) or "Prerequisites available"
        )
        if problems:
            raise typer.Exit(1)
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def plan(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    json_output: JsonOption = False,
    reinstall: ReinstallOption = False,
) -> None:
    """Plan using the resolved inputs also consumed by execution."""
    try:
        with service_context(cluster) as service:
            entries = service.plan(targets=node, reinstall=reinstall)
            _show(entries, json_output)
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def _show(entries, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps([asdict(entry) for entry in entries], indent=2))
    else:
        for entry in entries:
            typer.echo(f"{entry.id} {entry.hostname}: {entry.action}")


def _execute(
    operation: Operation,
    cluster: Path,
    node: list[int] | None,
    yes: bool,
    dry_run: bool,
    reinstall: bool,
    json_output: bool,
) -> None:
    try:
        with service_context(cluster) as service:
            entries = service.plan(operation, node, reinstall=reinstall)
            _show(entries, json_output)
            if dry_run:
                return
            if not yes:
                typer.confirm(
                    f"Execute {operation} for {len(entries)} node(s)?", abort=True
                )
            service.execute(operation, node, reinstall=reinstall)
    except (ValueError, OSError, RuntimeError, ExceptionGroup) as exc:
        _error(exc)


def up(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    reinstall: ReinstallOption = False,
    json_output: JsonOption = False,
) -> None:
    """Deploy, configure, and verify; preserve installed systems by default."""
    _execute("up", cluster, node, yes, dry_run, reinstall, json_output)


def deploy(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    reinstall: ReinstallOption = False,
    json_output: JsonOption = False,
) -> None:
    """Deploy and verify the OS without configuration."""
    _execute("deploy", cluster, node, yes, dry_run, reinstall, json_output)


def configure(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Apply the declared Ansible configuration to all selected nodes."""
    _execute("configure", cluster, node, yes, dry_run, False, json_output)


def down(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Stop nodes and preserve disks and reusable artifacts."""
    _execute("down", cluster, node, yes, dry_run, False, json_output)


def destroy(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Remove managed VMs; stop physical nodes and clean installation media."""
    _execute("destroy", cluster, node, yes, dry_run, False, json_output)


def status(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    json_output: JsonOption = False,
) -> None:
    """Report live state and retained failure information."""
    try:
        with service_context(cluster) as service:
            records = [
                {
                    "id": item.id,
                    "hostname": item.hostname,
                    "observed": asdict(service.backend.observe(item)),
                    **asdict(service.state.read(item.id)),
                }
                for item in service.cluster.nodes(node)
            ]
            typer.echo(
                json.dumps(records, indent=2)
                if json_output
                else "\n".join(
                    f"{entry['hostname']}: {entry['phase']} error={entry['error']}"
                    for entry in records
                )
            )
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)


def verify(
    cluster: ClusterOption = Path("cluster.yaml"),
    node: NodeOption = None,
    json_output: JsonOption = False,
) -> None:
    """Verify SSH access for every selected node, failing on any unreachable node."""
    results = []
    try:
        with service_context(cluster) as service:
            for item in service.cluster.nodes(node):
                try:
                    service.backend.verify(item)
                    results.append({"id": item.id, "ok": True})
                except (OSError, RuntimeError) as exc:
                    results.append({"id": item.id, "ok": False, "error": str(exc)})
        typer.echo(
            json.dumps(results, indent=2)
            if json_output
            else "\n".join(str(result) for result in results)
        )
        if not all(result["ok"] for result in results):
            raise typer.Exit(1)
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)
