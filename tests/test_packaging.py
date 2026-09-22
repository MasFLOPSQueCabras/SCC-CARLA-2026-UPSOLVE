import subprocess
import sys
from importlib import resources
from importlib.metadata import distribution

from cabritactl import __version__


def test_distribution_entry_point() -> None:
    entries = [
        entry.name
        for entry in distribution("cabritactl").entry_points
        if entry.group == "console_scripts"
    ]
    assert entries == ["cabritactl"]
    assert __version__ == distribution("cabritactl").version


def test_help_and_version_without_provider_imports() -> None:
    script = """
import sys
from typer.testing import CliRunner
from cabritactl.cli import app
for option in ('--help', '--version'):
    result = CliRunner().invoke(app, [option])
    assert result.exit_code == 0, result.output
assert not any(name.startswith(('cabritactl.providers.helvetios', 'cabritactl.providers.libvirt_backend', 'libvirt', 'httpx2')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)


def test_packaged_resources() -> None:
    expected = {
        "cabritactl": "ansible/playbooks/site.yaml",
        "cabritactl.providers.libvirt_backend": "templates/domain.xml.j2",
        "cabritactl.providers.helvetios": "configs/helvetios-hpc.yaml",
    }
    for package, resource in expected.items():
        assert resources.files(package).joinpath(resource).read_text()


def test_packaged_profiles_validate(monkeypatch):
    from cabritactl.core.manifest import parse_manifest

    monkeypatch.setenv("CABRITA_BMC_USER", "test-user")
    monkeypatch.setenv("CABRITA_BMC_PASSWORD", "test-password")
    for package in (
        "cabritactl.providers.libvirt_backend",
        "cabritactl.providers.helvetios",
    ):
        for resource in resources.files(package).joinpath("configs").iterdir():
            if resource.name.endswith(".yaml"):
                assert parse_manifest(resource.read_text()).nodes
