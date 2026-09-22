"""Real unattended installations; failed runs retain manifest, domain XML and logs."""

import hashlib
import importlib
import os
import shutil
import subprocess
import tempfile
import traceback
import uuid
from pathlib import Path

import pytest
import yaml

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.config import ClusterSettings
from cabrita.core.lifecycle.service import LifecycleService, ResourceLocks, StateStore
from cabrita.core.providers.base import ProviderPaths
from cabrita.core.resolved import ResolvedCluster
from cabrita.lifecycle import ProviderBackend


@pytest.mark.parametrize(
    "method,firmware",
    [
        pytest.param(
            method,
            firmware,
            marks=pytest.mark.e2e(provider="libvirt", bootstrap=method),
        )
        for method in ("cloud-init", "embedded-kickstart", "oemdrv")
        for firmware in ("bios", "efi")
    ],
)
def test_unattended_bootstrap(
    method: str, firmware: str, request: pytest.FixtureRequest, e2e_log_dir: Path
) -> None:
    source = request.config.getoption(
        "--cloud-image" if method == "cloud-init" else "--installer-iso"
    )
    if source is None or not source.is_file():
        pytest.fail(
            f"Missing prerequisite: provide {'--cloud-image' if method == 'cloud-init' else '--installer-iso'} with an existing file"
        )
    for executable in (
        "virsh",
        "qemu-img",
        "ssh",
        "ssh-keygen",
        "mkfs.vfat",
        "mcopy",
        "xorriso",
    ):
        if shutil.which(executable) is None:
            pytest.fail(f"Missing prerequisite executable: {executable}")
    root = Path(tempfile.mkdtemp(prefix="cabrita-e2e-", dir="/var/tmp"))
    root.chmod(0o755)
    provider = None
    try:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "key")],
            check=True,
            timeout=30,
        )
        document = {
            "name": f"acceptance-{uuid.uuid7().hex}",
            "provider": "libvirt",
            "access": {
                "public_key": str(root / "key.pub"),
                "private_key": str(root / "key"),
                "timeout": 1200,
            },
            "bootstrap": {"method": method, "artifact": "base"},
            "artifacts": {
                "base": {
                    "source": str(source),
                    "sha256": digest,
                    "format": "qcow2" if method == "cloud-init" else "iso",
                }
            },
            "defaults": {
                "os": {"username": "cabrita"},
                "vm": {
                    "vcpus": 2,
                    "memory_mb": 3072,
                    "firmware": firmware,
                    "graphics": "none",
                    "disk": {"size_gb": 16},
                },
            },
            "nodes": [
                {
                    "id": 11,
                    "hostname": "cabrita-test",
                    "ip": "192.168.122.211",
                    "mac": "52:54:00:cb:01:11",
                }
            ],
        }
        manifest = root / "cluster.yaml"
        manifest.write_text(yaml.safe_dump(document))
        cluster = ResolvedCluster.load(manifest)
        shutil.copyfile(manifest, e2e_log_dir / "cluster.yaml")
        module = importlib.import_module("cabrita.providers.libvirt_backend.provider")
        paths = ProviderPaths(root, root, root / "state.db", storage_dir=root / "disks")
        provider = module.LibvirtProvider(
            manifest=cluster.manifest, paths=paths, settings=ClusterSettings()
        )
        state = StateStore(root / "state")
        backend = ProviderBackend(
            cluster,
            provider,
            state,
            root / "work",
            root / "key.pub",
            root / "key",
            1200,
            ArtifactCache(root / "artifacts"),
        )
        service = LifecycleService(
            cluster, backend, state, ResourceLocks(root / "locks")
        )
        service.execute("up")
        assert state.read(11).phase == "ready"
        name = cluster.resource_name(cluster.nodes()[0])
        disk = provider.storage_dir / f"{name}.qcow2"
        inode = disk.stat().st_ino
        service.execute("up")
        assert disk.stat().st_ino == inode
        domain = provider.conn.lookupByName(name)
        xml = domain.XMLDesc(0)
        (e2e_log_dir / "domain.xml").write_text(xml)
        assert "device='cdrom'" not in xml
        assert "boot dev='hd'" in xml
        if firmware == "efi":
            assert "<loader" in xml
        command = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(root / "key"),
            "cabrita@192.168.122.211",
            "hostname; cat /etc/os-release; cat /proc/sys/kernel/random/boot_id",
        ]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=30
        )
        (e2e_log_dir / "verification.log").write_text(result.stdout + result.stderr)
        assert "cabrita-test" in result.stdout
        service.execute("destroy")
        assert not disk.exists()
    except Exception:
        (e2e_log_dir / "failure.log").write_text(traceback.format_exc())
        if (root / "work").exists():
            shutil.copytree(root / "work", e2e_log_dir / "work", dirs_exist_ok=True)
        if provider is not None:
            name = cluster.resource_name(cluster.nodes()[0])
            disk = provider.storage_dir / f"{name}.qcow2"
            if disk.exists() and shutil.which("virt-cat"):
                with (e2e_log_dir / "guest-cloud-init.log").open("w") as log:
                    subprocess.run(
                        [
                            "virt-cat",
                            "--format=qcow2",
                            "-a",
                            str(disk),
                            "/var/log/cloud-init.log",
                        ],
                        env={**os.environ, "LIBGUESTFS_BACKEND": "direct"},
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=120,
                        check=False,
                    )
        raise
    finally:
        if provider is not None:
            provider.teardown_node(11)
            provider.close()
        shutil.rmtree(root)
