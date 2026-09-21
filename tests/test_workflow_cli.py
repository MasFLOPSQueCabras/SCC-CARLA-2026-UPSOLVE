from contextlib import contextmanager
from pathlib import Path

import pytest
from test_lifecycle import Backend
from typer.testing import CliRunner

from cabrita.cli import app
from cabrita.commands import workflow
from cabrita.core.lifecycle.service import (
    LifecycleService,
    Observation,
    ResourceLocks,
    StateStore,
)
from cabrita.core.manifest import parse_manifest
from cabrita.core.resolved import ResolvedCluster


@pytest.mark.parametrize("command", ["up", "deploy", "configure", "down", "destroy"])
def test_cli_dry_run_uses_selected_nodes(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = parse_manifest("""name: cli
nodes:
  - {id: 7, hostname: first, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
  - {id: 8, hostname: second, ip: 192.0.2.8, mac: '52:54:00:00:00:08'}
""")
    backend = Backend()
    backend.nodes[8] = Observation(True, True, True)
    service = LifecycleService(
        ResolvedCluster(tmp_path / "cluster.yaml", manifest),
        backend,
        StateStore(tmp_path / "state"),
        ResourceLocks(tmp_path / "locks"),
    )

    @contextmanager
    def context(path: Path):
        yield service

    monkeypatch.setattr(workflow, "service_context", context)
    result = CliRunner().invoke(app, [command, "--node", "8", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    import json

    assert [entry["id"] for entry in json.loads(result.output)] == [8]
    assert not backend.deployed
    assert backend.configured == 0
    assert backend.nodes[8].running
