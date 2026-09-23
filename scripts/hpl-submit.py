#!/usr/bin/env python3
"""Assemble one validated result locally; never select incomplete/failed attempts."""

import argparse
import hashlib
import json
import re
import runpy
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("build_evidence", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--purpose", choices=("submission", "tuning"), default="submission"
    )
    args = parser.parse_args()
    purpose_note = (
        "Only this measured result is submitted."
        if args.purpose == "submission"
        else "This is a separate post-submission tuning package. The on-time submission is unchanged."
    )
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit):
        parser.error("--commit must be the full tested Cabrita commit hash")
    source = args.run.resolve()
    if (source / "exit-status.txt").read_text().strip() != "0":
        parser.error("The selected MPI run did not exit successfully")
    validate = runpy.run_path(str(Path(__file__).with_name("hpl-result.py")))["result"]
    result = validate(
        (source / "HPL.out").read_text(), (source / "HPL.dat").read_text()
    )
    for name in (
        "spack.yaml",
        "spack.lock",
        "compiler.txt",
        "spack-build-records.tar.gz",
        "hpl.sha256",
        "modified_source.zip",
        "source-changes.md",
    ):
        if not (args.build_evidence / name).is_file():
            parser.error(f"Missing build evidence: {name}")
    args.destination.mkdir(parents=True, exist_ok=False)
    dest = args.destination
    for name in ("input", "output", "scripts"):
        (dest / name).mkdir()
    shutil.copy2(source / "HPL.dat", dest / "input/HPL.dat")
    for name in (
        "HPL.out",
        "HPL.err",
        "exit-status.txt",
        "metadata.txt",
        "command.txt",
        "finished.txt",
    ):
        shutil.copy2(source / name, dest / "output" / name)
    (dest / "output/result.json").write_text(json.dumps(result, indent=2))
    for name in ("hpl-eval.sh", "hpl-result.py", "settings.sh", "hosts"):
        shutil.copy2(source / name, dest / "scripts" / name)
    # Relocate only the hostfile; retain the winning installation and binding settings.
    with (dest / "scripts/settings.sh").open("a") as settings:
        settings.write('\nMPI_HOSTFILE="$SUBMISSION_ROOT/scripts/hosts"\n')
    replay = dest / "scripts/run-hpl.sh"
    replay.write_text("""#!/usr/bin/env bash
set -euo pipefail
export SUBMISSION_ROOT
SUBMISSION_ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
exec bash "$SUBMISSION_ROOT/scripts/hpl-eval.sh" "$SUBMISSION_ROOT/input/HPL.dat" \\
    "$SUBMISSION_ROOT/scripts/settings.sh" "${1:?Pass a new result directory outside the submission}"
""")
    replay.chmod(0o755)
    shutil.copytree(
        args.build_evidence,
        dest / "scripts/build-evidence",
        ignore=shutil.ignore_patterns("modified_source.zip", "source-changes.md"),
    )
    (dest / "src").mkdir()
    shutil.copy2(
        args.build_evidence / "modified_source.zip", dest / "src/modified_source.zip"
    )
    shutil.copy2(args.build_evidence / "source-changes.md", dest / "src/README.md")
    description = args.build_evidence / "build-description.md"
    build_description = (
        description.read_text()
        if description.is_file()
        else """HPL 2.3, OpenMPI 5.0.5 with UCX, and OpenBLAS 0.3.28 are built using the pinned
Spack recipes and GCC C/Fortran compilers. OpenBLAS uses OpenMP threading and
the detected CPU target. UCX enables InfiniBand verbs, RC, UD, mlx5 direct verbs,
device memory and CMA. `scripts/build-evidence/spack.lock` records exact transitive
versions, variants and target architecture. `compiler.txt`, `spack-config.txt`,
`hpl-libraries.txt` and `spack-build-records.tar.gz` record compilers, build flags,
build environments and logs. No vendor HPL binary is used. The pinned Spack recipe rewrites the HPL configure
script’s BLAS probe. `src/modified_source.zip` contains that build-source tree;
`src/README.md` explains the change. The numerical algorithm is unchanged."""
    )
    (dest / "README.md").write_text(f"""# SCC@CARLA — team 72, Helvetios

Selected completed result: **{result["gflops"]:.6g} GFLOPS**; elapsed HPL time
{result["seconds"]} seconds; scaled residual {result["residual"]} (threshold 16.0).
N={result["n"]}, NB={result["nb"]}, process grid {result["p"]} × {result["q"]}.
{purpose_note} Full output and stderr are in `output/`.

## Reproduce the installation

Use [Cabrita (cabritactl)](https://github.com/MasFLOPSQueCabras/cabrita)
at commit `{args.commit}`. Install Python 3.14 and uv on the controller, then:

```bash
git clone https://github.com/MasFLOPSQueCabras/cabrita.git
cd cabrita
git checkout {args.commit}
uv sync --locked --extra helvetios
uv run ansible-galaxy collection install -r src/cabritactl/ansible/requirements.yml
```

Follow `competition/helvetios/README.md` at that commit for verified hardware,
ISO checksum, credentials, bastion prerequisites, staged installations, and smoke
checks. Reinstallation replaces the declared OS disks. Configuration uses the
source-built Spack stack; binary caches are disabled.

## Build and launch details

{build_description}

On the installed head node, with the shared installation at its original paths:

```bash
bash scripts/run-hpl.sh /shared/hpl/results/reproduction-$(date -u +%Y%m%dT%H%M%SZ)
```

The exact rank mapping, threading, UCX device and library paths are in
`scripts/settings.sh`; `output/command.txt` records the measured invocation.
The runner fails on MPI errors, timeouts, missing output, failed residual checks,
skipped tests or dimensions inconsistent with the input. Reproduction writes a
new directory and leaves the submitted result intact.
""")
    sums = []
    for path in sorted(dest.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            sums.append(f"{digest}  {path.relative_to(dest)}")
    (dest / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(dest)


if __name__ == "__main__":
    main()
