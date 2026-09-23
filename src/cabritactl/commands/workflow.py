"""Consistent lifecycle commands operating on a single resolved manifest."""

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from importlib.resources import files
from ipaddress import ip_network
from pathlib import Path
from typing import Annotated

import typer
import yaml

from cabritactl.bootstrap.artifacts import ArtifactCache
from cabritactl.config import ClusterSettings
from cabritactl.core.lifecycle.remote_locks import BastionLocks
from cabritactl.core.lifecycle.service import (
    LifecycleService,
    Operation,
    ResourceLocks,
    StateStore,
)
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.lifecycle import ProviderBackend
from cabritactl.paths import get_cache_dir, get_state_dir
from cabritactl.providers.factory import get_provider

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
        bastion_ssh_host=manifest.bastion.ssh_host,
        bastion_http_ip=manifest.bastion.http_bind_ip,
        bastion_http_port=manifest.bastion.http_port,
        bastion_serve_dir=manifest.bastion.remote_serve_dir,
        gateway_ip=manifest.network.gateway,
        dns_ip=manifest.network.dns,
    )
    state_root, cache_root = get_state_dir(), get_cache_dir()
    state = StateStore(cluster.state_directory(state_root))
    with get_provider(settings) as provider:
        backend = ProviderBackend(
            cluster,
            provider,
            state,
            cluster.state_directory(state_root) / "work",
            manifest.access.public_key,
            manifest.access.private_key,
            manifest.access.timeout,
            ArtifactCache(cache_root / "artifacts"),
        )
        yield LifecycleService(
            cluster,
            backend,
            state,
            ResourceLocks(state_root / "locks")
            if manifest.provider == "libvirt"
            else BastionLocks(manifest.bastion.ssh_host),
            max_workers=manifest.access.max_workers,
            libvirt_uri=settings.libvirt_uri,
        )


def _error(exc: Exception) -> None:
    def describe(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                describe(child)
            return
        text = str(error)
        typer.echo(text, err=True)
        if "apparmor" in text.lower():
            typer.echo(
                "Inspect virt-aa-helper and per-domain denials with sudo journalctl -k and the libvirt service journal. Keep AppArmor enabled; use managed storage and ordinary split UEFI firmware.",
                err=True,
            )
        elif "permission denied" in text.lower():
            typer.echo(
                "Check directory traversal and pool permissions, then SELinux AVC/AppArmor denials. A chmod change cannot override mandatory access control. See docs/host-installation.md.",
                err=True,
            )

    describe(exc)
    raise typer.Exit(1) from exc


def init(
    directory: Annotated[Path, typer.Argument()] = Path("."),
    provider: Annotated[str, typer.Option("--provider")] = "libvirt",
    network: Annotated[
        str,
        typer.Option(
            "--network", help="managed NAT or existing network (offline authoring)"
        ),
    ] = "managed",
    artifact: Annotated[
        Path | None,
        typer.Option(
            "--artifact", help="Local base image or installer ISO; checksum is computed"
        ),
    ] = None,
) -> None:
    """Create a cluster manifest and editable templates from packaged resources."""
    packages = {
        "libvirt": ("cabritactl.providers.libvirt_backend", "vm-standard.yaml"),
        "helvetios": ("cabritactl.providers.helvetios", "helvetios-hpc.yaml"),
    }
    if provider not in packages:
        raise typer.BadParameter("Provider must be libvirt or helvetios")
    package, filename = packages[provider]
    directory = directory.resolve()
    destination = directory / "cluster.yaml"
    if destination.exists():
        raise typer.BadParameter(f"Manifest already exists: {destination}")
    document = yaml.safe_load(files(package).joinpath("configs", filename).read_text())
    if provider == "libvirt":
        if network not in ("managed", "existing"):
            raise typer.BadParameter("--network must be managed or existing")
        document["name"] = re.sub(r"[^a-z0-9-]", "-", directory.name.lower())[:40]
        if not document["name"] or not document["name"][0].isalpha():
            document["name"] = "cluster-" + document["name"]
        if network == "managed":
            try:
                import libvirt

                from cabritactl.providers.libvirt_backend.network import author_network

                connection = libvirt.openReadOnly(ClusterSettings().libvirt_uri)
                if connection is None:
                    raise RuntimeError("Cannot connect to libvirt")
                try:
                    document["network"].update(
                        author_network(connection, document["name"])
                    )
                finally:
                    connection.close()
            except Exception as exc:
                raise typer.BadParameter(
                    f"Managed network discovery failed: {exc}. Run host setup, or use --network existing for offline authoring."
                ) from exc
            subnet = ip_network(document["network"]["subnet"])
            prefix = hashlib.sha256(document["name"].encode()).hexdigest()[:6]
            for i, node in enumerate(document["nodes"], 101):
                node["ip"] = str(subnet[i])
                node["mac"] = f"52:54:{prefix[:2]}:{prefix[2:4]}:{prefix[4:6]}:{i:02x}"
        document["defaults"]["vm"]["graphics"] = "none"
        for node in document["nodes"]:
            node.pop("vm", None)
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
    document.setdefault(
        "configuration",
        {"profile": "none" if provider == "libvirt" else "scc-carla-2026"},
    )
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
        from cabritactl.host.doctor import collect, report

        result = report(collect(ResolvedCluster.load(cluster)))
        typer.echo(
            json.dumps(result, indent=2)
            if json_output
            else "\n".join(
                f"{c['status'].upper()} {c['name']}: {c['detail']}"
                + (
                    f"\n  Fix: {c['remedy']}"
                    if c["remedy"] and c["status"] != "pass"
                    else ""
                )
                for c in result["checks"]
            )
        )
        if not result["ok"]:
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
            if operation in ("up", "deploy"):
                from cabritactl.host.doctor import collect, report

                preflight = report(collect(service.cluster))
                if not preflight["ok"]:
                    raise RuntimeError(
                        "Preflight failed:\n"
                        + "\n".join(preflight["problems"])
                        + "\nRun cabritactl doctor --cluster "
                        + str(cluster)
                    )
            service.execute(operation, node, reinstall=reinstall)
            if (
                operation == "destroy"
                and service.cluster.manifest.provider == "libvirt"
            ):
                cleanup = getattr(service.backend.provider, "cleanup_network", None)
                if cleanup is not None:
                    cleanup()
    except (
        ValueError,
        OSError,
        RuntimeError,
        ExceptionGroup,
        subprocess.SubprocessError,
    ) as exc:
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
    network: Annotated[
        bool, typer.Option("--network", help="Also check guest networking, NFS and MPI")
    ] = False,
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
        if network:
            from cabritactl.host.doctor import runtime_checks

            checks = runtime_checks(ResolvedCluster.load(cluster))
            results.extend(
                {"check": c.name, "ok": c.status == "pass", "detail": c.detail}
                for c in checks
            )
        typer.echo(
            json.dumps(results, indent=2)
            if json_output
            else "\n".join(str(result) for result in results)
        )
        if not all(result["ok"] for result in results):
            raise typer.Exit(1)
    except (ValueError, OSError, RuntimeError) as exc:
        _error(exc)
