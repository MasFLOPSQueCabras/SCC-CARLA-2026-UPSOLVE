"""Optional real UFW adapter test in an isolated rootless network namespace."""

import io
import subprocess
import tarfile
from pathlib import Path

import pytest


@pytest.mark.e2e(provider="host", bootstrap="firewall")
def test_ubuntu_ufw_container(e2e_log_dir):
    root = Path(__file__).resolve().parents[2]
    wheels = sorted(
        (root / "dist").glob("cabritactl-*.whl"), key=lambda p: p.stat().st_mtime
    )
    if not wheels:
        pytest.fail("Run uv build first")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        tar.add(wheels[-1], arcname=wheels[-1].name)
        tar.add(root / "tests/fixtures/ufw_acceptance.py", arcname="ufw_acceptance.py")
    script = """set -eu
mkdir -p /tmp/check
tar -xzf - -C /tmp/check
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv ufw iproute2 nftables
python3 -m venv /tmp/venv
/tmp/venv/bin/pip install --quiet /tmp/check/*.whl
/tmp/venv/bin/python /tmp/check/ufw_acceptance.py
"""
    result = subprocess.run(
        [
            "podman",
            "run",
            "--rm",
            "-i",
            "--cap-add=NET_ADMIN",
            "docker.io/library/ubuntu:26.04",
            "bash",
            "-c",
            script,
        ],
        input=archive.getvalue(),
        capture_output=True,
        check=False,
        timeout=600,
    )
    (e2e_log_dir / "ufw.log").write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode()[-6000:]
