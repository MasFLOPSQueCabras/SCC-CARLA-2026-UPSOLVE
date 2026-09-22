"""Capture a running service, destroy its source, and restore two fresh identities."""

import hashlib
import importlib
import json
import os
import pty
import shutil
import subprocess
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Literal

import pytest
import yaml

from cabritactl.bootstrap.artifacts import ArtifactCache
from cabritactl.bootstrap.golden import capture
from cabritactl.config import ClusterSettings
from cabritactl.core.lifecycle.service import (
    LifecycleService,
    ResourceLocks,
    StateStore,
)
from cabritactl.core.providers.base import ProviderPaths
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.lifecycle import ProviderBackend


def artifact(path: Path, format: str) -> dict[str, str]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"source": str(path), "format": format, "sha256": digest}


def ssh(
    root: Path, address: str, command: str, *, key: str = "key", check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(root / key),
            f"cabrita@{address}",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    with (root / "ssh.log").open("a") as log:
        log.write(f"{address}: {command}\n{result.stdout}{result.stderr}\n")
    if check:
        result.check_returncode()
    return result


@pytest.mark.parametrize("firmware", ["bios", "efi"])
@pytest.mark.e2e(provider="libvirt", bootstrap="golden-restore")
def test_capture_destroy_and_restore_independent_nodes(
    request: pytest.FixtureRequest, e2e_log_dir: Path, firmware: Literal["bios", "efi"]
) -> None:
    cloud, iso = (
        request.config.getoption("--cloud-image"),
        request.config.getoption("--installer-iso"),
    )
    if cloud is None or iso is None or not cloud.is_file() or not iso.is_file():
        pytest.fail(
            "Missing prerequisite: --cloud-image and --installer-iso must name existing files"
        )
    for executable in (
        "qemu-img",
        "virt-inspector",
        "virt-sysprep",
        "zstd",
        "xorriso",
        "ssh-keygen",
    ):
        if shutil.which(executable) is None:
            pytest.fail(f"Missing prerequisite: {executable}")
    root = Path(tempfile.mkdtemp(prefix="cabrita-golden-e2e-", dir="/var/tmp"))
    root.chmod(0o755)
    providers = []
    services = []
    stopped = threading.Event()
    libvirt = importlib.import_module("libvirt")

    def collect_consoles():
        consoles = {}
        try:
            while not stopped.wait(0.5):
                for service in tuple(services):
                    provider = service.backend.provider
                    for node in service.cluster.nodes():
                        name = service.cluster.resource_name(node)
                        if name in consoles and consoles[name][0].poll() is None:
                            continue
                        domain = provider._get_domain(node.id)
                        if domain is None:
                            continue
                        try:
                            if not domain.isActive():
                                continue
                        except libvirt.libvirtError as exc:
                            if exc.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                                continue  # Teardown can remove the domain after lookup.
                            raise
                        if name in consoles:
                            _, master = consoles.pop(name)
                            os.close(master)
                        master, slave = pty.openpty()
                        with (e2e_log_dir / f"{name}.console.log").open("ab") as log:
                            process = subprocess.Popen(
                                [
                                    "virsh",
                                    "-c",
                                    "qemu:///system",
                                    "console",
                                    name,
                                    "--force",
                                ],
                                stdin=slave,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                            )
                        os.close(slave)
                        consoles[name] = process, master
        finally:
            for process, master in consoles.values():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                os.close(master)

    collector = threading.Thread(target=collect_consoles)
    collector.start()
    try:
        for name in ("key", "old-key"):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
                check=True,
                timeout=30,
            )
        base = {
            "name": "golden-source-" + uuid.uuid7().hex,
            "provider": "libvirt",
            "access": {
                "public_key": str(root / "old-key.pub"),
                "private_key": str(root / "old-key"),
                "timeout": 900,
            },
            "bootstrap": {"method": "cloud-init", "artifact": "base"},
            "artifacts": {"base": artifact(cloud, "qcow2")},
            "defaults": {
                "os": {"username": "cabrita"},
                "vm": {
                    "vcpus": 2,
                    "memory_mb": 4096,
                    "firmware": firmware,
                    "graphics": "none",
                    "disk": {"size_gb": 16},
                },
            },
            "nodes": [
                {
                    "id": 21,
                    "hostname": "golden-source",
                    "ip": "192.168.122.221",
                    "mac": "52:54:00:ca:02:21",
                }
            ],
        }

        def make_service(document):
            directory = root / document["name"]
            directory.mkdir()
            path = directory / "cluster.yaml"
            path.write_text(yaml.safe_dump(document))
            resolved = ResolvedCluster.load(path)
            module = importlib.import_module(
                "cabritactl.providers.libvirt_backend.provider"
            )
            provider = module.LibvirtProvider(
                manifest=resolved.manifest,
                settings=ClusterSettings(),
                paths=ProviderPaths(
                    directory,
                    directory,
                    directory / "state.db",
                    storage_dir=directory / "disks",
                ),
            )
            _ = (
                provider.conn
            )  # Open before the console thread can observe this service.
            providers.append(provider)
            state = StateStore(directory / "state")
            backend = ProviderBackend(
                resolved,
                provider,
                state,
                directory / "work",
                resolved.manifest.access.public_key,
                resolved.manifest.access.private_key,
                900,
                ArtifactCache(root / "artifacts"),
            )
            service = LifecycleService(
                resolved, backend, state, ResourceLocks(root / "locks")
            )
            services.append(service)
            return service

        original = make_service(base)
        original.execute("up")
        setup = """sudo mkdir -p /opt/cabrita-golden
printf 'prepared-once' | sudo tee /opt/cabrita-golden/index.html
printf '[Unit]\nDescription=Captured service\nAfter=network.target\n[Service]\nExecStart=/usr/bin/python3 -m http.server 8080 --directory /opt/cabrita-golden\n[Install]\nWantedBy=multi-user.target\n' | sudo tee /etc/systemd/system/cabrita-golden.service
sudo systemctl daemon-reload && sudo systemctl enable --now cabrita-golden.service
"""
        ssh(root, "192.168.122.221", setup, key="old-key")
        ssh(
            root,
            "192.168.122.221",
            "curl --ipv4 --fail --retry 10 --retry-connrefused --retry-delay 1 --retry-max-time 30 --max-time 5 http://localhost:8080/",
            key="old-key",
        )
        original.execute("down")
        (node,) = original.cluster.nodes()
        disk = (
            original.backend.provider.storage_dir
            / f"{original.cluster.resource_name(node)}.qcow2"
        )
        metadata = capture(
            disk,
            root / "golden",
            firmware=firmware,
            username="cabrita",
            provisioning={"service": "cabrita-golden"},
        )
        original.execute("destroy")
        assert not disk.exists()
        assert (root / "golden/golden.qcow2").is_file()
        base["name"] = "golden-restored-" + uuid.uuid7().hex
        base["access"].update(
            public_key=str(root / "key.pub"), private_key=str(root / "key")
        )
        base["bootstrap"] = {
            "method": "golden-restore",
            "artifact": "installer",
            "payload": "golden",
            "metadata": "metadata",
            "inputs": {"http_port": 18072},
        }
        base["artifacts"] = {
            "installer": artifact(iso, "iso"),
            "golden": artifact(root / "golden/golden.raw.zst", "raw.zst"),
            "metadata": artifact(metadata, "json"),
        }
        base["nodes"] = [
            {
                "id": n,
                "hostname": f"restored-{n}",
                "ip": f"192.168.122.2{n}",
                "mac": f"52:54:00:ca:02:{n}",
            }
            for n in (22, 23)
        ]
        restored = make_service(base)
        restored.execute("up")
        restored.execute("up")
        identities = []
        for node in restored.cluster.nodes():
            result = ssh(
                root,
                node.ip,
                "hostname; cat /etc/machine-id; sudo cat /etc/ssh/ssh_host_ed25519_key.pub; curl --ipv4 --fail --retry 10 --retry-connrefused --retry-delay 1 --retry-max-time 30 --max-time 5 http://localhost:8080/",
            )
            (e2e_log_dir / f"node-{node.id}.log").write_text(
                result.stdout + result.stderr
            )
            lines = result.stdout.splitlines()
            assert lines[0] == node.hostname
            assert lines[-1] == "prepared-once"
            identities.append(lines[1:3])
            assert (
                node.ip in ssh(root, node.ip, f"getent ahostsv4 {node.hostname}").stdout
            )
            assert "golden-source" not in ssh(root, node.ip, "cat /etc/hosts").stdout
            assert (
                ssh(root, node.ip, "true", key="old-key", check=False).returncode != 0
            )
            xml = restored.backend.provider.conn.lookupByName(
                restored.cluster.resource_name(node)
            ).XMLDesc(0)
            assert "device='cdrom'" not in xml
            assert "boot dev='hd'" in xml
        assert identities[0][0] != identities[1][0]
        assert identities[0][1] != identities[1][1]
        restored.execute("destroy")
        assert (root / "golden/golden.raw.zst").exists()
        (e2e_log_dir / "result.json").write_text(
            json.dumps(
                {"nodes": 2, "captured_service": True, "distinct_identities": True}
            )
        )
    except BaseException:
        (e2e_log_dir / "failure.log").write_text(traceback.format_exc())
        for service in services:
            for node in service.cluster.nodes():
                if service.backend._reachable(node):
                    result = ssh(
                        root,
                        node.ip,
                        "sudo journalctl -u cabrita-golden.service --no-pager -n 50; "
                        "sudo systemctl status cabrita-golden.service --no-pager; "
                        "sudo ss -lntp; ip address; cat /etc/hosts; "
                        "curl -v --max-time 5 http://localhost:8080/; "
                        "curl -v --max-time 5 http://127.0.0.1:8080/",
                        key=service.backend.private_key.name,
                        check=False,
                    )
                    (e2e_log_dir / f"service-{node.id}.log").write_text(
                        result.stdout + result.stderr
                    )
            directory = service.backend.work_dir
            if directory.exists():
                shutil.copytree(
                    directory,
                    e2e_log_dir / service.cluster.identity,
                    dirs_exist_ok=True,
                )
        for capture_dir in root.glob(".*.capture-*"):
            if (capture_dir / "capture.log").exists():
                shutil.copyfile(
                    capture_dir / "capture.log", e2e_log_dir / "capture.log"
                )
        for provider in providers:
            for path in provider.storage_dir.glob("*.console.log"):
                shutil.copyfile(path, e2e_log_dir / path.name)
        if (root / "ssh.log").exists():
            shutil.copyfile(root / "ssh.log", e2e_log_dir / "ssh.log")
        raise
    finally:
        stopped.set()
        collector.join(timeout=15)
        for service in services:
            for node in service.cluster.nodes():
                service.backend.provider.teardown_node(node.id)
            service.backend.provider.close()
        shutil.rmtree(root)
