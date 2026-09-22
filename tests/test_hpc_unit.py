from pathlib import Path

import pytest

from cabrita.core.hpc import HPCSettings
from cabrita.core.manifest import parse_manifest


def manifest():
    return parse_manifest("""name: hpc
configuration:
  profile: lightweight
nodes:
  - {id: 7, hostname: head, role: headnode, ip: 192.0.2.7, mac: '52:54:00:00:00:07'}
  - {id: 9, hostname: worker, ip: 192.0.2.9, mac: '52:54:00:00:00:09'}
""")


def test_hpc_defaults_derive_from_declared_nodes():
    cluster = manifest()
    settings = HPCSettings.resolve(cluster)
    assert settings.hpl_q is not None
    assert settings.hpl_p * settings.hpl_q == 2
    assert settings.nfs_server == "head"
    assert settings.variables(cluster)["cluster_addresses"] == {
        "head": "192.0.2.7",
        "worker": "192.0.2.9",
    }


def test_spack_preserves_user_selected_mpi_paths():
    cluster = manifest()
    cluster.configuration.inputs = {
        "software": "spack",
        "mpi_launcher": "/opt/mpi/bin/mpirun",
        "mpi_library_path": "/opt/mpi/lib",
    }
    settings = HPCSettings.resolve(cluster)
    assert settings.mpi_launcher == "/opt/mpi/bin/mpirun"
    assert settings.mpi_library_path == "/opt/mpi/lib"


def test_shared_configuration_rejects_empty_cluster():
    cluster = manifest()
    cluster.nodes = []
    with pytest.raises(ValueError, match="at least one declared node"):
        HPCSettings.resolve(cluster)


@pytest.mark.parametrize(
    "inputs,message",
    [
        ({"hpl_p": 3}, "divide"),
        ({"hpl_q": 3}, "must equal"),
        ({"transport": "ucx"}, "every declared"),
        ({"nfs_server": "unknown"}, "declared node"),
    ],
)
def test_invalid_hpc_inputs_fail_before_configuration(inputs, message):
    cluster = manifest()
    cluster.configuration.inputs = inputs
    with pytest.raises(ValueError, match=message):
        HPCSettings.resolve(cluster)


def test_hpl_numerical_failures_are_not_successes():
    import runpy
    from importlib.resources import files

    validate = runpy.run_path(
        str(files("cabrita").joinpath("ansible/roles/hpl/files/check_result.py"))
    )["validate"]
    passed = "1 tests completed and passed residual checks\n0 tests completed and failed residual checks\n0 tests skipped because of illegal input values"
    validate(passed)
    for output in (
        "",
        passed.replace("0 tests completed and failed", "1 tests completed and failed"),
        passed.replace("0 tests skipped", "1 tests skipped"),
    ):
        with pytest.raises(ValueError, match="numerical"):
            validate(output)


def test_competition_init_preserves_valid_shared_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from typer.testing import CliRunner

    from cabrita.cli import app
    from cabrita.core.resolved import ResolvedCluster

    monkeypatch.setenv("CABRITA_BMC_USER", "test-user")
    monkeypatch.setenv("CABRITA_BMC_PASSWORD", "test-password")
    artifact = tmp_path / "installer.iso"
    artifact.write_bytes(b"installer")
    directory = tmp_path / "competition"
    result = CliRunner().invoke(
        app,
        [
            "init",
            str(directory),
            "--provider",
            "helvetios",
            "--artifact",
            str(artifact),
        ],
    )
    assert result.exit_code == 0, result.output
    cluster = ResolvedCluster.load(directory / "cluster.yaml")
    settings = HPCSettings.resolve(cluster.manifest)
    assert settings.ib_addresses.keys() == {node.hostname for node in cluster.nodes()}
    assert settings.software == "spack"


def test_shared_configuration_refuses_missing_nodes_before_ansible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from unittest.mock import Mock

    from cabrita.bootstrap.artifacts import ArtifactCache
    from cabrita.core.lifecycle.service import StateStore
    from cabrita.core.providers.base import NodeProvider
    from cabrita.core.resolved import ResolvedCluster
    from cabrita.lifecycle import ProviderBackend

    cluster = ResolvedCluster(tmp_path / "cluster.yaml", manifest())
    backend = ProviderBackend(
        cluster,
        Mock(spec=NodeProvider),
        StateStore(tmp_path / "state"),
        tmp_path,
        tmp_path / "key.pub",
        tmp_path / "key",
        30,
        ArtifactCache(tmp_path / "cache"),
    )
    run = Mock()
    monkeypatch.setattr("cabrita.lifecycle.subprocess.run", run)
    with pytest.raises(ValueError, match="all declared nodes"):
        backend.configure(cluster.nodes([7]))
    run.assert_not_called()
