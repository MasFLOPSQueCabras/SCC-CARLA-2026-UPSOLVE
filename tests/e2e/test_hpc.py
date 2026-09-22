"""Two-node shared configuration and recovery without rebuilding software."""

import importlib
import json
import shutil
import subprocess
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_golden import artifact, ssh

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.bootstrap.golden import capture
from cabrita.config import ClusterSettings
from cabrita.core.lifecycle.service import LifecycleService, ResourceLocks, StateStore
from cabrita.core.providers.base import ProviderPaths
from cabrita.core.resolved import ResolvedCluster
from cabrita.lifecycle import ProviderBackend


@pytest.mark.e2e(provider="libvirt", bootstrap="golden-restore")
def test_shared_hpc_and_golden_recovery(
    request: pytest.FixtureRequest, e2e_log_dir: Path
) -> None:
    cloud = request.config.getoption("--cloud-image")
    iso = request.config.getoption("--installer-iso")
    if cloud is None or iso is None or not cloud.is_file() or not iso.is_file():
        pytest.fail("Missing prerequisite: provide --cloud-image and --installer-iso")
    root = Path(tempfile.mkdtemp(prefix="cabrita-hpc-e2e-", dir="/var/tmp"))
    root.chmod(0o755)
    services = []
    try:
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "key")],
            check=True,
            timeout=30,
        )
        document: dict[str, Any] = {
            "name": "hpc-source-" + uuid.uuid7().hex,
            "provider": "libvirt",
            "access": {
                "public_key": str(root / "key.pub"),
                "private_key": str(root / "key"),
                "timeout": 1800,
            },
            "bootstrap": {"method": "cloud-init", "artifact": "base"},
            "artifacts": {"base": artifact(cloud, "qcow2")},
            "configuration": {
                "profile": "lightweight",
                "inputs": {"hpl_n": 1024, "hpl_nb": 64},
            },
            "defaults": {
                "os": {"username": "cabrita"},
                "vm": {
                    "vcpus": 2,
                    "memory_mb": 4096,
                    "graphics": "none",
                    "disk": {"size_gb": 16},
                },
            },
        }

        def make_service(start: int):
            document["nodes"] = [
                {
                    "id": n,
                    "hostname": f"hpc-{n}",
                    "ip": f"192.168.122.2{n}",
                    "mac": f"52:54:00:ca:03:{n}",
                    "role": "headnode" if n == start else "computenode",
                }
                for n in (start, start + 1)
            ]
            directory = root / document["name"]
            directory.mkdir()
            path = directory / "cluster.yaml"
            path.write_text(yaml.safe_dump(document))
            cluster = ResolvedCluster.load(path)
            module = importlib.import_module(
                "cabrita.providers.libvirt_backend.provider"
            )
            provider = module.LibvirtProvider(
                manifest=cluster.manifest,
                settings=ClusterSettings(),
                paths=ProviderPaths(
                    directory,
                    directory,
                    directory / "state.db",
                    storage_dir=directory / "disks",
                ),
            )
            state = StateStore(directory / "state")
            backend = ProviderBackend(
                cluster,
                provider,
                state,
                directory / "work",
                root / "key.pub",
                root / "key",
                1800,
                ArtifactCache(root / "artifacts"),
            )
            service = LifecycleService(
                cluster, backend, state, ResourceLocks(root / "locks")
            )
            services.append(service)
            return service

        def verify(service):
            head, worker = service.cluster.nodes()
            ssh(root, head.ip, f"ssh {worker.hostname} hostname")
            ssh(root, head.ip, "printf shared-storage > /shared/acceptance.txt")
            assert (
                ssh(root, worker.ip, "cat /shared/acceptance.txt").stdout
                == "shared-storage"
            )
            mpi = ssh(
                root,
                head.ip,
                "/usr/lib64/openmpi/bin/mpirun -np 2 --hostfile /shared/hpl/hosts --mca pml ob1 --mca btl self,tcp hostname",
            )
            assert set(mpi.stdout.splitlines()) == {head.hostname, worker.hostname}
            result = ssh(root, head.ip, "/shared/hpl/run_hpl.sh")
            (e2e_log_dir / f"{service.cluster.identity}-hpl.log").write_text(
                result.stdout + result.stderr
            )
            assert "HPL numerical checks passed" in result.stdout
            return ssh(
                root,
                head.ip,
                "sha256sum /shared/hpl/hpl-2.3/bin/cabrita/xhpl; stat -c %Y /shared/hpl/hpl-2.3/bin/cabrita/xhpl",
            ).stdout

        original = make_service(24)
        original.execute("up")
        binary = verify(original)
        original.execute("configure")
        assert verify(original) == binary
        original.execute("down")
        node = original.cluster.nodes()[0]
        disk = (
            original.backend.provider.storage_dir
            / f"{original.cluster.resource_name(node)}.qcow2"
        )
        metadata = capture(
            disk,
            root / "golden",
            firmware="bios",
            username="cabrita",
            provisioning={"configuration": document["configuration"]},
        )
        original.execute("destroy")
        document["name"] = "hpc-restored-" + uuid.uuid7().hex
        # Rocky's installer reserves 20% of RAM for /run, where recovery verifies
        # the complete compressed software payload before touching the disk.
        document["defaults"]["vm"]["memory_mb"] = 8192
        document["bootstrap"] = {
            "method": "golden-restore",
            "artifact": "installer",
            "payload": "golden",
            "metadata": "metadata",
            "inputs": {"http_port": 18072},
        }
        document["artifacts"] = {
            "installer": artifact(iso, "iso"),
            "golden": artifact(root / "golden/golden.raw.zst", "raw.zst"),
            "metadata": artifact(metadata, "json"),
        }
        restored = make_service(26)
        restored.execute("up")
        assert verify(restored) == binary
        restored.execute("up")
        (e2e_log_dir / "result.json").write_text(
            json.dumps(
                {
                    "nfs": True,
                    "mpi": True,
                    "hpl": True,
                    "recovered_without_rebuild": True,
                }
            )
        )
    except BaseException:
        (e2e_log_dir / "failure.log").write_text(traceback.format_exc())
        for service in services:
            for node in service.cluster.nodes():
                try:
                    ssh(
                        root,
                        node.ip,
                        "hostname; findmnt /shared; sudo cat /proc/fs/nfsd/v4_end_grace; "
                        "sudo exportfs -v; sudo journalctl -b -u nfs-server --no-pager; "
                        "sudo dmesg | tail -40",
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    with (root / "ssh.log").open("a") as log:
                        log.write(f"Diagnostics timed out for {node.hostname}: {exc}\n")
        raise
    finally:
        for service in services:
            if service.backend.work_dir.exists():
                shutil.copytree(
                    service.backend.work_dir,
                    e2e_log_dir / service.cluster.identity,
                    dirs_exist_ok=True,
                )
            for node in service.cluster.nodes():
                service.backend.provider.teardown_node(node.id)
            service.backend.provider.close()
        if (root / "ssh.log").exists():
            shutil.copyfile(root / "ssh.log", e2e_log_dir / "ssh.log")
        for path in root.glob("*/capture.log"):
            shutil.copyfile(path, e2e_log_dir / f"{path.parent.name}-capture.log")
        shutil.rmtree(root)
