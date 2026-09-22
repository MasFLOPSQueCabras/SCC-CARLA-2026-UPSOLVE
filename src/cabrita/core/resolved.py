"""Resolve a manifest once at the application boundary."""

import hashlib
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from cabrita.core.bootstrap import BootstrapMethod
from cabrita.core.hpc import HPCSettings
from cabrita.core.manifest import ClusterManifest, NodeSpec, load_manifest


@dataclass(frozen=True, slots=True)
class ResolvedCluster:
    source: Path
    manifest: ClusterManifest
    hpc: HPCSettings | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hpc",
            HPCSettings.resolve(self.manifest)
            if self.manifest.configuration.profile in ("lightweight", "scc-carla-2026")
            else None,
        )

    @classmethod
    def load(cls, source: Path) -> ResolvedCluster:
        source = source.expanduser().resolve()
        manifest = load_manifest(source)
        for artifact in manifest.artifacts.values():
            if urlparse(artifact.source).scheme not in ("https", "http"):
                artifact.source = str(
                    (source.parent / artifact.source.removeprefix("file://"))
                    .expanduser()
                    .resolve()
                )
        for attribute in ("public_key", "private_key"):
            value = getattr(manifest.access, attribute).expanduser()
            setattr(manifest.access, attribute, (source.parent / value).resolve())
        bootstrap = manifest.bootstrap
        for attribute in ("user_data", "network_config", "kickstart", "templates"):
            value = getattr(bootstrap, attribute)
            if value is not None:
                setattr(
                    bootstrap, attribute, (source.parent / value).expanduser().resolve()
                )
        if bootstrap.prepare and bootstrap.prepare.execution == "local":
            executable = bootstrap.prepare.argv[0]
            if "/" in executable:
                bootstrap.prepare.argv[0] = str((source.parent / executable).resolve())
        if manifest.configuration.playbook:
            manifest.configuration.playbook = (
                source.parent / manifest.configuration.playbook
            ).resolve()
        cluster = cls(source, manifest)
        cluster.validate()
        return cluster

    @property
    def identity(self) -> str:
        return self.manifest.name

    def state_directory(self, state_root: Path) -> Path:
        return state_root / "clusters" / self.identity

    def nodes(self, requested: list[int] | None = None) -> tuple[NodeSpec, ...]:
        declared = {node.id: node for node in self.manifest.nodes}
        targets = set(requested) if requested else set(declared)
        if unknown := targets - declared.keys():
            raise ValueError(f"Undeclared node IDs: {sorted(unknown)}")
        return tuple(declared[node] for node in sorted(targets))

    def resource_name(self, node: NodeSpec) -> str:
        if node.id not in {n.id for n in self.manifest.nodes}:
            raise ValueError(f"Undeclared node: {node.id}")
        return f"cabrita-{self.identity}-node{node.id}"

    def lock_key(self, node: NodeSpec, libvirt_uri: str = "qemu:///system") -> str:
        match self.manifest.provider:
            case "libvirt":
                resource = f"{libvirt_uri}/{self.resource_name(node)}"
            case "helvetios":
                if node.bmc is None:
                    raise ValueError(f"Node {node.id} has no BMC")
                resource = f"bmc://{node.bmc.ip}:{node.bmc.port}"
            case _:
                raise ValueError(f"Unsupported provider: {self.manifest.provider}")
        return hashlib.sha256(resource.encode()).hexdigest()

    def validate(self) -> None:
        manifest = self.manifest
        if not manifest.nodes:
            raise ValueError("Cluster must declare at least one node")
        method = manifest.bootstrap.method
        match manifest.provider, method:
            case "libvirt", _:
                pass
            case (("helvetios"), BootstrapMethod.CLOUD_INIT):
                raise ValueError("Helvetios does not support cloud-init disk overlays")
            case (("helvetios"), _):
                if any(node.bmc is None for node in manifest.nodes):
                    raise ValueError("Helvetios nodes require BMC addresses")
            case _:
                raise ValueError(f"Unsupported provider: {manifest.provider}")
        if method == BootstrapMethod.CUSTOM:
            return
        reference = manifest.bootstrap.artifact
        if not reference or reference not in manifest.artifacts:
            raise ValueError(
                "Bootstrap requires a declared artifact reference and SHA-256 checksum"
            )
        expected_format = "qcow2" if method == BootstrapMethod.CLOUD_INIT else "iso"
        if manifest.artifacts[reference].format != expected_format:
            raise ValueError(f"{method} requires a {expected_format} artifact")
        if method == BootstrapMethod.GOLDEN_RESTORE:
            payload = manifest.bootstrap.payload
            if (
                not payload
                or payload not in manifest.artifacts
                or manifest.artifacts[payload].format != "raw.zst"
            ):
                raise ValueError("Golden restore requires a declared raw.zst payload")
            metadata = manifest.bootstrap.metadata
            if (
                not metadata
                or metadata not in manifest.artifacts
                or manifest.artifacts[metadata].format != "json"
            ):
                raise ValueError("Golden restore requires declared JSON metadata")

    def recovery_endpoint(self) -> tuple[str, int]:
        if self.manifest.provider == "helvetios":
            bastion = self.manifest.bastion
            return bastion.http_bind_ip, bastion.http_port
        inputs = self.manifest.bootstrap.inputs
        host = str(inputs.get("http_bind_ip", self.manifest.network.gateway))
        port = int(inputs.get("http_port", 8072))
        ip_address(host)
        if not 1 <= port <= 65535:
            raise ValueError("Recovery HTTP port must be between 1 and 65535")
        return host, port
