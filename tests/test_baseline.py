import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.cli import app
from cabrita.core.bootstrap import ArtifactSpec
from cabrita.core.manifest import parse_manifest
from cabrita.core.resolved import ResolvedCluster


def test_nested_manifest_inheritance(manifest_text: str) -> None:
    manifest = parse_manifest(manifest_text)
    vm = manifest.nodes[0].vm
    assert vm is not None
    assert (vm.vcpus, vm.disk.size_gb, vm.disk.bus) == (8, 100, "scsi")
    assert manifest.defaults.vm.disk.size_gb == 80


def test_duplicate_nodes_rejected(manifest_text: str) -> None:
    with pytest.raises(ValueError, match="Duplicate node id"):
        parse_manifest(
            manifest_text
            + """
  - id: 1
    hostname: worker
    ip: 192.0.2.2
    mac: '52:54:00:00:00:02'
"""
        )


@pytest.mark.parametrize(
    "targets,expected", [(None, [7, 9]), ([9, 7, 9], [7, 9]), ([9], [9])]
)
def test_node_targeting(tmp_path: Path, targets, expected):
    cluster = ResolvedCluster(
        tmp_path / "cluster.yaml",
        parse_manifest("""name: targets
nodes:
  - {id: 7, hostname: head, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
  - {id: 9, hostname: worker, ip: 192.0.2.9, mac: '52:54:00:00:00:09'}
"""),
    )
    assert [node.id for node in cluster.nodes(targets)] == expected
    with pytest.raises(ValueError, match="Undeclared"):
        cluster.nodes([99])


def test_explicit_artifact_selected(tmp_path: Path):
    source = tmp_path / "selected.qcow2"
    source.write_bytes(b"explicit image")
    cache = ArtifactCache(tmp_path / "cache")
    cache.directory.mkdir()
    (cache.directory / "default.qcow2").write_bytes(b"unrelated cached image")
    spec = ArtifactSpec(
        source=str(source),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        format="qcow2",
    )
    assert cache.materialize(spec).read_bytes() == source.read_bytes()


def test_deploy_cli_failure_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    from cabrita.commands import workflow

    def unavailable(path: Path):
        raise RuntimeError("Provider unavailable")

    monkeypatch.setattr(workflow, "service_context", unavailable)
    result = CliRunner().invoke(app, ["deploy", "--yes"])
    assert result.exit_code == 1
