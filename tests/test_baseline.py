from pathlib import Path
from unittest.mock import Mock

import pytest
from rich.progress import Progress
from scc_core.manifest import parse_manifest
from scc_core.parallel import ParallelRunner
from typer.testing import CliRunner

from scc_carla.cli import app
from scc_carla.commands import deploy
from scc_carla.config import ClusterSettings
from scc_carla.db import NodeLifecycle
from scc_carla.image import ensure_cached_cloud_image
from scc_carla.nodes import resolve_target_nodes


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
    "targets, expected", [(None, [1, 2, 3]), ([3, 1, 3], [1, 3]), (2, [2])]
)
def test_node_targeting(targets: list[int] | int | None, expected: list[int]) -> None:
    assert resolve_target_nodes(targets) == expected


def test_invalid_target_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid node"):
        resolve_target_nodes([1, 99])


def test_parallel_failure_is_retained() -> None:
    error = RuntimeError("provider unavailable")

    def task(node: int) -> int:
        if node == 2:
            raise error
        return node

    results = ParallelRunner[int, int]().items([1, 2]).task(task).run()
    assert results[1].success
    assert not results[2].success
    assert results[2].error is error


def test_explicit_artifact_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    artifact = tmp_path / "selected.qcow2"
    artifact.write_bytes(b"explicit image")
    monkeypatch.setattr("scc_carla.image.get_image_cache_dir", lambda: cache)
    settings = ClusterSettings(cloud_image_source="missing-default.qcow2")
    result = ensure_cached_cloud_image(settings, str(artifact))
    assert result.read_bytes() == artifact.read_bytes()
    assert result == cache / artifact.name


def test_configuration_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = Mock()
    monkeypatch.setattr(deploy, "update_node_state", states)
    monkeypatch.setattr(deploy, "is_ssh_authenticated", lambda *a, **kw: True)
    monkeypatch.setattr(deploy, "configure_command", lambda *a, **kw: False)
    provider = Mock()
    provider.provision_node.return_value = True
    with Progress(disable=True) as progress:
        succeeded = deploy._provision_single_node(
            ClusterSettings(),
            1,
            "test-key",
            Mock(),
            tmp_path,
            provider,
            10,
            progress,
            progress.add_task("test"),
            run_ansible=True,
        )
    assert not succeeded
    assert states.call_args.args[2] == NodeLifecycle.BOOTSTRAPPED


def test_deploy_cli_failure_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(deploy, "deploy_command", lambda *a, **kw: False)
    result = CliRunner().invoke(app, ["deploy"])
    assert result.exit_code == 1
