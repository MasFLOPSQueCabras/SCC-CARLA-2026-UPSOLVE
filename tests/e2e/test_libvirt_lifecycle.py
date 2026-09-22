"""Real libvirt persistence/destruction contract; no guest image is required."""

import importlib
import subprocess
import uuid
from pathlib import Path

import pytest

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.config import ClusterSettings
from cabrita.core.lifecycle.service import (
    Checkpoint,
    LifecycleService,
    ResourceLocks,
    StateStore,
)
from cabrita.core.manifest import parse_manifest
from cabrita.core.providers.base import ProviderPaths
from cabrita.core.resolved import ResolvedCluster
from cabrita.lifecycle import ProviderBackend


@pytest.mark.e2e(provider="libvirt", bootstrap="lifecycle")
def test_stopped_vm_disk_preserved_then_destroyed(
    tmp_path: Path, e2e_log_dir: Path
) -> None:
    try:
        module = importlib.import_module("cabrita.providers.libvirt_backend.provider")
        name = f"acceptance-{uuid.uuid7().hex}"
        manifest = parse_manifest(f"""name: {name}
nodes:
  - {{id: 9, hostname: guest, ip: 192.0.2.9, mac: '52:54:00:00:00:09'}}
""")
        cluster = ResolvedCluster(tmp_path / "cluster.yaml", manifest)
        paths = ProviderPaths(
            tmp_path, tmp_path, tmp_path / "state.db", storage_dir=tmp_path
        )
        with module.LibvirtProvider(
            manifest=manifest, paths=paths, settings=ClusterSettings()
        ) as provider:
            domain_name = cluster.resource_name(manifest.nodes[0])
            disk = tmp_path / f"{domain_name}.qcow2"
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk), "16M"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            xml = f"""<domain type='kvm'><name>{domain_name}</name><memory unit='MiB'>128</memory><vcpu>1</vcpu><os><type arch='x86_64'>hvm</type></os><devices><disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{disk}'/><target dev='vda' bus='virtio'/></disk></devices></domain>"""
            provider.conn.defineXML(xml)
            state = StateStore(tmp_path / "state")
            backend = ProviderBackend(
                cluster,
                provider,
                state,
                tmp_path / "work",
                tmp_path / "key.pub",
                tmp_path / "key",
                10,
                ArtifactCache(tmp_path / "artifacts"),
            )
            service = LifecycleService(
                cluster, backend, state, ResourceLocks(tmp_path / "locks")
            )
            state.write(9, Checkpoint("ready", service.fingerprint))
            try:
                service.execute("down")
                assert disk.is_file()
                assert provider.node_exists(9)
                service.execute("destroy")
                assert not disk.exists()
                assert not provider.node_exists(9)
                assert state.read(9).phase == "new"
            finally:
                provider.teardown_node(9)
    except Exception as exc:
        (e2e_log_dir / "failure.log").write_text(repr(exc))
        raise
