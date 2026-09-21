import subprocess
import sys
from importlib import resources
from importlib.metadata import distribution

from cabrita import __version__


def test_distribution_entry_point() -> None:
    entries = [
        entry.name
        for entry in distribution("cabrita").entry_points
        if entry.group == "console_scripts"
    ]
    assert entries == ["cabrita"]
    assert __version__ == distribution("cabrita").version


def test_help_and_version_without_provider_imports() -> None:
    script = """
import sys
from typer.testing import CliRunner
from cabrita.cli import app
for option in ('--help', '--version'):
    result = CliRunner().invoke(app, [option])
    assert result.exit_code == 0, result.output
assert not any(name.startswith(('cabrita.providers.helvetios', 'cabrita.providers.libvirt_backend', 'libvirt', 'httpx2')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)


def test_packaged_resources() -> None:
    expected = {
        "cabrita": "ansible/playbooks/site.yaml",
        "cabrita.providers.libvirt_backend": "templates/domain.xml.j2",
        "cabrita.providers.helvetios": "configs/helvetios-hpc.yaml",
    }
    for package, resource in expected.items():
        assert resources.files(package).joinpath(resource).read_text()
