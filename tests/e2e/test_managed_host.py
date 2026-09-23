"""Real managed-pool, NAT, confinement and three-node HPC acceptance."""

import hashlib
import importlib
import json
import platform
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

from cabritactl.commands.workflow import service_context
from cabritactl.host.doctor import collect, report, runtime_checks
from cabritactl.providers.libvirt_backend.network import author_network


@pytest.mark.e2e(provider="libvirt", bootstrap="cloud-init")
@pytest.mark.parametrize("firmware", ["bios", "efi"])
def test_managed_three_node_hpc(
    request, tmp_path: Path, e2e_log_dir: Path, monkeypatch, firmware
):
    cloud = request.config.getoption("--cloud-image")
    if cloud is None or not cloud.is_file():
        pytest.fail("Provide --cloud-image")
    libvirt = importlib.import_module("libvirt")
    conn = libvirt.open("qemu:///system")
    name = "managed-" + uuid.uuid4().hex[:12]
    pool = conn.storagePoolDefineXML(
        f'<pool type="dir"><name>{name}</name><target><path>/var/lib/libvirt/images/{name}</path></target></pool>',
        0,
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    service = None
    try:
        pool.build(0)
        pool.create(0)
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(tmp_path / "key"),
            ],
            check=True,
        )
        with cloud.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        network = author_network(conn, name)
        prefix = network["gateway"].rsplit(".", 1)[0]
        document = {
            "name": name,
            "provider": "libvirt",
            "libvirt": {"storage_pool": name},
            "network": network,
            "access": {
                "public_key": str(tmp_path / "key.pub"),
                "private_key": str(tmp_path / "key"),
                "timeout": 1800,
            },
            "artifacts": {
                "os": {"source": str(cloud), "sha256": digest, "format": "qcow2"}
            },
            "bootstrap": {"method": "cloud-init", "artifact": "os"},
            "configuration": {
                "profile": "lightweight",
                "inputs": {
                    "hpl_n": 1024,
                    "hpl_nb": 64,
                    "management_sources": [network["gateway"]],
                },
            },
            "defaults": {
                "os": {"username": "cabrita"},
                "vm": {
                    "vcpus": 2,
                    "memory_mb": 3072,
                    "disk": {"size_gb": 12},
                    "graphics": "none",
                    "firmware": firmware,
                },
            },
            "nodes": [
                {
                    "id": i,
                    "hostname": f"node{i}",
                    "role": "headnode" if i == 1 else "computenode",
                    "ip": f"{prefix}.{100 + i}",
                    "mac": f"52:54:{name[-6:-4]}:{name[-4:-2]}:{name[-2:]}:{i:02x}",
                }
                for i in range(1, 4)
            ],
        }
        path = tmp_path / "cluster.yaml"
        path.write_text(yaml.safe_dump(document))
        with service_context(path) as service:
            preflight = report(collect(service.cluster))
            (e2e_log_dir / "doctor.json").write_text(json.dumps(preflight, indent=2))
            assert preflight["ok"], preflight["problems"]
            service.execute("up")
            checks = runtime_checks(service.cluster)
            (e2e_log_dir / "runtime.json").write_text(
                json.dumps([c.__dict__ for c in checks], indent=2)
            )
            assert all(c.status == "pass" for c in checks), checks
            head = document["nodes"][0]["ip"]
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-i",
                str(tmp_path / "key"),
                f"cabrita@{head}",
                "/shared/hpl/run_hpl.sh",
            ]
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300, check=False
            )
            (e2e_log_dir / "hpl.log").write_text(result.stdout + result.stderr)
            assert (
                result.returncode == 0
                and "HPL numerical checks passed" in result.stdout
            )
            # Boot/retry state survives removal of the disposable cache.
            shutil.rmtree(tmp_path / "cache")
            service.execute("down")
            with service.backend.provider.capture_source(
                1, service.cluster.resource_name(service.cluster.nodes([1])[0])
            ) as captured:
                subprocess.run(
                    ["qemu-img", "check", str(captured)],
                    check=True,
                    capture_output=True,
                )
                assert captured.is_file()
            assert not captured.exists()
            service.execute("up")
            service.execute("up")
            assert all(service.state.read(i).phase == "ready" for i in range(1, 4))
            (e2e_log_dir / "host.json").write_text(
                json.dumps(platform.freedesktop_os_release(), indent=2)
            )
    finally:
        if service is not None:
            work = service.backend.work_dir
            if work.exists():
                shutil.copytree(work, e2e_log_dir / "work", dirs_exist_ok=True)
            # Reuse the still-open, already-authorized connection. A fresh
            # connection can prompt polkit after its temporary authorization expires.
            provider_module = importlib.import_module(
                "cabritactl.providers.libvirt_backend.provider"
            )
            cleanup = provider_module.LibvirtProvider(service.cluster.manifest)
            cleanup._conn = conn
            for node in service.cluster.nodes():
                cleanup.teardown_node(node.id)
            cleanup.cleanup_network()
        if pool.isActive():
            for volume in pool.listAllVolumes():
                volume.delete(0)
            pool.destroy()
        pool.delete(0)
        pool.undefine()
        conn.close()
