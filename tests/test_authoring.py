import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from cabritactl.core.bootstrap import CustomPreparer, PreparationSpec
from cabritactl.core.di import create_registry
from cabritactl.core.resolved import ResolvedCluster


@pytest.fixture
def authoring_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "cluster.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "test-cluster",
                "provider": "libvirt",
                "bootstrap": {
                    "method": "cloud-init",
                    "artifact": "base",
                    "user_data": "user-data.yaml",
                },
                "artifacts": {
                    "base": {
                        "source": "base.qcow2",
                        "sha256": "a" * 64,
                        "format": "qcow2",
                    }
                },
                "nodes": [
                    {
                        "id": i,
                        "hostname": f"worker-{i}",
                        "ip": f"192.0.2.{i}",
                        "mac": f"52:54:00:00:00:{i:02x}",
                    }
                    for i in range(10, 16)
                ],
            }
        )
    )
    return path


def test_resolved_paths_and_arbitrary_targets(authoring_manifest: Path) -> None:
    cluster = ResolvedCluster.load(authoring_manifest)
    assert [node.id for node in cluster.nodes()] == list(range(10, 16))
    assert [node.id for node in cluster.nodes([15, 10, 15])] == [10, 15]
    assert (
        cluster.manifest.bootstrap.user_data
        == authoring_manifest.parent / "user-data.yaml"
    )
    assert cluster.manifest.artifacts["base"].source == str(
        authoring_manifest.parent / "base.qcow2"
    )
    with pytest.raises(ValueError, match="Undeclared"):
        cluster.nodes([1])


def test_cluster_and_shared_hardware_isolation(authoring_manifest: Path) -> None:
    first = ResolvedCluster.load(authoring_manifest)
    data = yaml.safe_load(authoring_manifest.read_text())
    data["name"] = "second-cluster"
    authoring_manifest.write_text(yaml.safe_dump(data))
    second = ResolvedCluster.load(authoring_manifest)
    assert first.state_directory(authoring_manifest.parent) != second.state_directory(
        authoring_manifest.parent
    )
    assert first.resource_name(first.nodes()[0]) != second.resource_name(
        second.nodes()[0]
    )
    assert first.lock_key(first.nodes()[0]) != second.lock_key(second.nodes()[0])
    for cluster in (first, second):
        cluster.manifest.provider = "helvetios"
        from cabritactl.core.manifest import BMCSpec

        cluster.nodes()[0].bmc = BMCSpec(ip="192.0.2.100")
    assert first.lock_key(first.nodes()[0]) == second.lock_key(second.nodes()[0])


def test_unsupported_provider_method(authoring_manifest: Path) -> None:
    data = yaml.safe_load(authoring_manifest.read_text())
    data["provider"] = "helvetios"
    authoring_manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="does not support cloud-init"):
        ResolvedCluster.load(authoring_manifest)
    with pytest.raises(ValueError, match="Unknown provider"):
        create_registry().get("chameleon")
    assert "chameleon" not in create_registry().list_providers()


def test_missing_artifact_rejected(authoring_manifest: Path) -> None:
    data = yaml.safe_load(authoring_manifest.read_text())
    data["artifacts"] = {}
    authoring_manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="artifact reference"):
        ResolvedCluster.load(authoring_manifest)


def test_custom_preparation_contract(tmp_path: Path) -> None:
    script = tmp_path / "prepare.py"
    script.write_text("""import argparse, hashlib, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--context')
parser.add_argument('--output')
args = parser.parse_args()
context = json.loads(Path(args.context).read_text())
artifact = Path(args.output).parent / 'custom.iso'
artifact.write_bytes(context['name'].encode())
Path(args.output).write_text(json.dumps({'source': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'format': 'iso'}))
""")
    result = CustomPreparer().prepare(
        PreparationSpec(argv=[sys.executable, str(script)]),
        {"name": "example"},
        tmp_path / "work",
    )
    assert result.artifact.sha256 == hashlib.sha256(b"example").hexdigest()
    assert Path(result.path).read_bytes() == b"example"


def test_preparation_failure_and_stale_output(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text("{}")
    with pytest.raises(subprocess.CalledProcessError):
        CustomPreparer().prepare(
            PreparationSpec(argv=[sys.executable, "-c", "raise SystemExit(4)"]),
            {},
            tmp_path,
        )
    assert not (tmp_path / "artifact.json").exists()
    assert "status 4" in (tmp_path / "prepare.log").read_text()


def test_preparation_timeout(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        CustomPreparer().prepare(
            PreparationSpec(
                argv=[sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1
            ),
            {},
            tmp_path,
        )
    assert (tmp_path / "prepare.log").exists()


def test_bastion_preparation_contract(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"prepared").hexdigest()
    calls: list[list[str]] = []

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert timeout == 15
        if argv[0] == "scp" and argv[-1] == str(tmp_path / "artifact.json"):
            Path(argv[-1]).write_text(
                json.dumps(
                    {"source": "/srv/custom.iso", "sha256": digest, "format": "iso"}
                )
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            digest + "  /srv/custom.iso\n" if "sha256sum" in argv[-1] else "",
            "",
        )

    result = CustomPreparer(run).prepare(
        PreparationSpec(
            argv=["prepare", "literal;arg"], execution="bastion", timeout=15
        ),
        {},
        tmp_path,
        bastion="test-host",
        remote_dir="/srv/work",
    )
    assert result.execution == "bastion"
    assert any("'literal;arg'" in argv[-1] for argv in calls)


def test_registries_are_independent() -> None:
    first, second = create_registry(), create_registry()
    first.register("testing", Mock())
    assert not second.is_registered("testing")


@pytest.mark.parametrize("provider", ["vm", "bmc", "chi", "chameleon"])
def test_legacy_provider_names_rejected(authoring_manifest: Path, provider: str):
    document = yaml.safe_load(authoring_manifest.read_text())
    document["provider"] = provider
    authoring_manifest.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        ResolvedCluster.load(authoring_manifest)
    with pytest.raises(ValueError, match="Unknown provider"):
        create_registry().get(provider)
