"""Competition correctness checks without requiring deployment infrastructure."""

import runpy
from pathlib import Path

import pytest
from jinja2 import Template

ROOT = Path(__file__).resolve().parents[1]
HELPERS = runpy.run_path(str(ROOT / "scripts/hpl-result.py"))
TUNING = runpy.run_path(str(ROOT / "scripts/hpl-tune.py"))


@pytest.fixture
def dat():
    template = ROOT / "src/cabritactl/ansible/roles/hpl/templates/HPL.dat.j2"
    return Template(template.read_text()).render(
        hpl_n=4096, hpl_nb=128, hpl_p=1, hpl_q=3
    )


@pytest.fixture
def output():
    return """WR11C2R4 4096 128 1 3 0.25 1.8300e+02
||Ax-b||_oo/(eps*(||A||_oo*||x||_oo+||b||_oo)*N)= 3.34e-03 ...... PASSED
1 tests completed and passed residual checks,
0 tests completed and failed residual checks,
0 tests skipped because of illegal input values.
End of Tests.
"""


def test_result_preserves_exact_input_and_performance(dat, output):
    result = HELPERS["result"](output, dat)
    assert result["gflops"] == 183
    assert result["n"] == 4096
    assert result["residual"] == 0.00334


@pytest.mark.parametrize(
    "old,new",
    [
        ("End of Tests.", ""),
        ("4096 128 1 3", "8192 128 1 3"),
        ("1.8300e+02", "nan"),
        ("1.8300e+02", "-100"),
        ("3.34e-03", "inf"),
        ("3.34e-03", "16.0"),
        ("0 tests skipped", "1 tests skipped"),
        ("0 tests completed and failed", "1 tests completed and failed"),
    ],
)
def test_invalid_results_cannot_be_submitted(dat, output, old, new):
    with pytest.raises(ValueError):
        HELPERS["result"](output.replace(old, new), dat)


def test_multiple_results_cannot_be_submitted(dat, output):
    with pytest.raises(ValueError):
        HELPERS["result"](output + output, dat)


def test_disabled_residual_checks_are_rejected(dat):
    with pytest.raises(ValueError, match="threshold"):
        HELPERS["input_case"](dat.replace("16.0", "-16.0"))


def test_memory_limit_aligns_blocks_and_leaves_workspace():
    memory = 192 * 1024**3
    for p, q in TUNING["grids"](108):
        n = TUNING["matrix_limit"](memory, 3, 0.8, 192, p, q)
        assert n % (192 * p) == 0
        assert n % (192 * q) == 0
        assert 8 * n * n / 3 < 0.8 * memory


def test_generated_case_matches_launched_grid(dat):
    text = TUNING["write_input"](dat, 32768, 256, 2, 3)
    assert HELPERS["input_case"](text) == {"n": 32768, "nb": 256, "p": 2, "q": 3}


@pytest.mark.parametrize("status", [0, 7])
@pytest.mark.parametrize("implementation", ["openmpi", "intel"])
def test_eval_captures_mpi_failure_and_preserves_results(
    tmp_path, dat, output, status, implementation
):
    import shlex
    import subprocess

    dat_path = tmp_path / "HPL.dat"
    dat_path.write_text(dat)
    fixture = tmp_path / "fixture.out"
    fixture.write_text(output)
    launcher = tmp_path / "mpirun"
    launcher.write_text("""#!/usr/bin/env bash
if [[ $1 == --version ]]; then echo 'Test MPI fixture'; exit 0; fi
cat "$FAKE_OUTPUT"
echo 'MPI diagnostic' >&2
exit "$FAKE_STATUS"
""")
    launcher.chmod(0o755)
    hosts = tmp_path / "hosts"
    hosts.write_text("node1 slots=1\nnode2 slots=1\nnode3 slots=1\n")
    settings = tmp_path / "settings.sh"
    values = {
        "HPL_BINARY": "/bin/true",
        "MPI_LAUNCHER": launcher,
        "MPI_HOSTFILE": hosts,
        "HPL_RANKS": 3,
        "OMP_NUM_THREADS": 1,
        "MPI_MAP_BY": "ppr:1:node:PE=1",
        "MPI_LIBRARY_PATH": "/usr/lib64",
        "UCX_NET_DEVICES": "test:1",
        "HPL_TIMEOUT_SECONDS": 5,
        "HPL_LOCK_FILE": tmp_path / "eval.lock",
        "FAKE_OUTPUT": fixture,
        "FAKE_STATUS": status,
        "MPI_IMPLEMENTATION": implementation,
    }
    settings.write_text(
        "\n".join(f"export {k}={shlex.quote(str(v))}" for k, v in values.items())
    )
    destination = tmp_path / "result"
    command = [
        "bash",
        str(ROOT / "scripts/hpl-eval.sh"),
        str(dat_path),
        str(settings),
        str(destination),
    ]
    process = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=15
    )
    assert process.returncode == status, process.stderr
    if implementation == "intel":
        assert (destination / "hosts.intel").read_text() == "node1\nnode2\nnode3\n"
    assert (destination / "HPL.err").read_text() == "MPI diagnostic\n"
    assert (destination / "exit-status.txt").read_text().strip() == str(status)
    assert (destination / "result.json").exists() is (status == 0)
    repeated = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=15
    )
    assert repeated.returncode != 0
    assert (destination / "HPL.out").read_text() == output


@pytest.mark.parametrize(
    "method,expected", [("embedded-kickstart", False), ("oemdrv", True)]
)
def test_bastion_preflight_requires_floppy_tools_only_for_oemdrv(
    tmp_path, monkeypatch, method, expected
):
    from unittest.mock import Mock

    from cabritactl.bootstrap.remote import BastionMedia
    from cabritactl.core.manifest import parse_manifest
    from cabritactl.core.resolved import ResolvedCluster
    from cabritactl.core.templating import TemplateEngine

    manifest = parse_manifest(f'''name: remote-check
provider: helvetios
bastion:
  remote_serve_dir: /tmp/team-media
bootstrap:
  method: {method}
  build_on: bastion
  artifact: installer
artifacts:
  installer:
    source: https://example.test/installer.iso
    sha256: "{"0" * 64}"
    format: iso
nodes:
  - id: 1
    hostname: node1
    ip: 192.0.2.1
    mac: '52:54:00:00:00:01'
    bmc:
      ip: 192.0.2.2
''')
    remote = BastionMedia(
        ResolvedCluster(tmp_path / "cluster.yaml", manifest), TemplateEngine()
    )
    checked = []

    def command(argv, **kwargs):
        if argv[0] == "python3":
            checked.extend(argv[3:])
            raise RuntimeError("Stop after prerequisite inspection")
        return Mock(returncode=0, stdout="")

    monkeypatch.setattr(remote, "_remote", command)
    with pytest.raises(RuntimeError, match="Stop after"):
        remote.prepare(manifest.nodes[0], "ssh-ed25519 test", tmp_path, Mock())
    assert ("mkfs.vfat" in checked) is expected
    assert "mcopy" in checked  # Embedded EFI boot menus also live on FAT media.
    assert "xorriso" in checked


@pytest.mark.parametrize("custom_build", [False, True])
def test_submission_packages_one_valid_result_with_source(
    tmp_path, dat, output, custom_build
):
    import subprocess
    import sys

    run = tmp_path / "run"
    run.mkdir()
    (run / "HPL.dat").write_text(dat)
    (run / "HPL.out").write_text(output)
    (run / "exit-status.txt").write_text("0\n")
    for name in (
        "HPL.err",
        "metadata.txt",
        "command.txt",
        "finished.txt",
        "settings.sh",
        "hosts",
    ):
        (run / name).write_text("test fixture\n")
    for name in ("hpl-eval.sh", "hpl-result.py"):
        (run / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    build = tmp_path / "build"
    build.mkdir()
    for name in (
        "spack.yaml",
        "spack.lock",
        "compiler.txt",
        "spack-build-records.tar.gz",
        "hpl.sha256",
        "modified_source.zip",
        "source-changes.md",
    ):
        (build / name).write_text("test fixture\n")
    if custom_build:
        (build / "build-description.md").write_text(
            "Self-built HPL with oneMKL fixture."
        )
    package = tmp_path / "submission"
    command = [
        sys.executable,
        str(ROOT / "scripts/hpl-submit.py"),
        str(run),
        str(build),
        str(package),
        "--commit",
        "a" * 40,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert (package / "input/HPL.dat").read_text() == dat
    assert (package / "output/HPL.out").read_text() == output
    assert (package / "src/modified_source.zip").is_file()
    readme = (package / "README.md").read_text()
    assert "cabritactl" in readme
    if custom_build:
        assert "Self-built HPL with oneMKL fixture." in readme
        assert "OpenBLAS 0.3.28" not in readme
    assert not (package / "scripts/build-evidence/modified_source.zip").exists()
    checked = subprocess.run(
        ["sha256sum", "--check", "SHA256SUMS"],
        cwd=package,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0
    repeated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert repeated.returncode != 0
