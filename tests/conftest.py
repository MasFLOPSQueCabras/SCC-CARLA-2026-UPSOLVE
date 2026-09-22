from pathlib import Path
from uuid import uuid7

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("deployment")
    group.addoption(
        "--run-e2e", action="store_true", help="Enable infrastructure tests"
    )
    group.addoption(
        "--provider", action="append", default=[], help="Select e2e providers"
    )
    group.addoption(
        "--bootstrap", action="append", default=[], help="Select bootstrap methods"
    )
    group.addoption(
        "--e2e-log-dir", default="test-results/e2e", help="Retained deployment logs"
    )
    group.addoption(
        "--installer-iso", type=Path, help="Local installer ISO for real e2e tests"
    )
    group.addoption(
        "--cloud-image", type=Path, help="Local cloud qcow2 for real e2e tests"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        marker = item.get_closest_marker("e2e")
        if marker is None:
            continue
        if not config.getoption("--run-e2e"):
            item.add_marker(
                pytest.mark.skip(
                    reason="requires --run-e2e and deployment infrastructure"
                )
            )
        for option in ("provider", "bootstrap"):
            selected = config.getoption(f"--{option}")
            if selected and marker.kwargs.get(option) not in selected:
                item.add_marker(
                    pytest.mark.skip(reason=f"excluded by --{option} selection")
                )


@pytest.fixture
def e2e_log_dir(request: pytest.FixtureRequest) -> Path:
    path = (
        Path(request.config.getoption("--e2e-log-dir"))
        / request.node.name
        / uuid7().hex
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def manifest_text() -> str:
    return """
name: regression
provider: libvirt
defaults:
  vm:
    vcpus: 8
    disk:
      size_gb: 80
      bus: scsi
nodes:
  - id: 1
    hostname: head
    ip: 192.0.2.1
    mac: '52:54:00:00:00:01'
    vm:
      disk:
        size_gb: 100
"""
