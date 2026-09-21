from pathlib import Path

import pytest

from cabrita.core.lifecycle.service import (
    LifecycleService,
    Observation,
    ResourceLocks,
    StateStore,
)
from cabrita.core.manifest import parse_manifest
from cabrita.core.resolved import ResolvedCluster


class Backend:
    def __init__(self) -> None:
        self.nodes: dict[int, Observation] = {}
        self.deployed: list[int] = []
        self.configured = 0
        self.fail: set[int] = set()

    def observe(self, node):
        return self.nodes.get(node.id, Observation(False, False, False))

    def deploy(self, node, *, reinstall):
        self.deployed.append(node.id)
        if node.id in self.fail:
            raise TimeoutError("Installation timeout")
        self.nodes[node.id] = Observation(True, True, True)

    def start(self, node):
        self.nodes[node.id] = Observation(True, True, True)

    def verify(self, node):
        if not self.observe(node).reachable:
            raise TimeoutError("SSH unavailable")

    def configure(self, nodes):
        self.configured += 1

    def stop(self, node):
        self.nodes[node.id] = Observation(True, False, False)

    def destroy(self, node):
        del self.nodes[node.id]


@pytest.fixture
def service(tmp_path: Path) -> LifecycleService[Backend]:
    manifest = parse_manifest("""name: test
nodes:
  - {id: 7, hostname: first, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
  - {id: 8, hostname: second, ip: 192.0.2.8, mac: '52:54:00:00:00:08'}
""")
    return LifecycleService(
        ResolvedCluster(tmp_path / "cluster.yaml", manifest),
        Backend(),
        StateStore(tmp_path / "state"),
        ResourceLocks(tmp_path / "locks"),
    )


def test_repeated_up_preserves_installations(
    service: LifecycleService[Backend],
) -> None:
    service.execute("up")
    service.execute("up")
    assert sorted(service.backend.deployed) == [7, 8]
    assert service.backend.configured == 1
    assert all(service.state.read(node).phase == "ready" for node in (7, 8))


def test_partial_failure_resumes_and_releases_locks(
    service: LifecycleService[Backend],
) -> None:
    service.backend.fail.add(8)
    with pytest.raises(ExceptionGroup):
        service.execute("up")
    assert service.state.read(7).phase == "bootstrapped"
    assert service.state.read(8).error == "Installation timeout"
    service.backend.fail.clear()
    service.execute("up")
    assert service.backend.deployed.count(7) == 1
    assert service.backend.deployed.count(8) == 2
    assert service.state.read(8).error is None


def test_down_preserves_disks_and_up_restarts(
    service: LifecycleService[Backend],
) -> None:
    service.execute("up")
    service.execute("down")
    assert service.backend.nodes[7].exists
    assert not service.backend.nodes[7].running
    service.execute("up")
    assert service.backend.nodes[7].running
    assert len(service.backend.deployed) == 2


def test_unmanaged_and_replacement_protection(
    service: LifecycleService[Backend],
) -> None:
    service.backend.nodes[7] = Observation(True, True, True)
    with pytest.raises(RuntimeError, match="unmanaged"):
        service.execute("up")
    service.execute("up", reinstall=True)
    del service.backend.nodes[7]
    with pytest.raises(RuntimeError, match="replacement-required"):
        service.execute("up")


def test_lock_contention_and_plan_targets(service: LifecycleService[Backend]) -> None:
    node = service.cluster.nodes([7])[0]
    with (
        service.locks.acquire([service.cluster.lock_key(node)]),
        pytest.raises(RuntimeError, match="locked"),
    ):
        service.execute("up", [7])
    planned = service.plan(targets=[8])
    executed = service.execute("up", [8])
    assert [entry.id for entry in planned] == [entry.id for entry in executed] == [8]
    assert service.backend.deployed == [8]


def test_destroy_leaves_artifact_cache(
    service: LifecycleService[Backend], tmp_path: Path
) -> None:
    cache = tmp_path / "artifact-cache"
    cache.mkdir()
    artifact = cache / "golden.raw.zst"
    artifact.write_bytes(b"reusable")
    service.execute("up")
    service.execute("destroy")
    assert service.backend.nodes == {}
    assert artifact.read_bytes() == b"reusable"


def test_configuration_change_does_not_reinstall(
    service: LifecycleService[Backend],
) -> None:
    service.execute("up")
    service.cluster.manifest.configuration.inputs["message"] = "updated"
    service.execute("up")
    assert len(service.backend.deployed) == 2
    assert service.backend.configured == 2


def test_configuration_failure_retries_without_reinstall(
    service: LifecycleService[Backend], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.backend.configure

    def fail(nodes):
        raise RuntimeError("Ansible failed")

    monkeypatch.setattr(service.backend, "configure", fail)
    with pytest.raises(RuntimeError, match="Ansible failed"):
        service.execute("up")
    assert service.state.read(7).phase == "bootstrapped"
    assert service.state.read(7).error == "Ansible failed"
    monkeypatch.setattr(service.backend, "configure", original)
    service.execute("up")
    assert len(service.backend.deployed) == 2
    assert service.state.read(7).phase == "ready"
