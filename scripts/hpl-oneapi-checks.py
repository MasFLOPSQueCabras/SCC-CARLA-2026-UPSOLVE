#!/usr/bin/env python3
"""Compare self-built HPL toolchains on the verified three-node allocation."""

import argparse
import json
import runpy
import shlex
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / "hpl-tune.py"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    template = Path("/shared/hpl/HPL.dat").read_text()
    base = Path("/shared/hpl/hpl-settings.sh").read_text()
    records = []
    variants = ("gcc-mkl-openmpi", "icx-mkl-openmpi", "icx-mkl-intelmpi")
    layouts = (
        (36, 1, "ppr:36:node:PE=1", 6, 18),
        (2, 18, "ppr:1:numa:PE=18", 2, 3),
        (4, 9, "ppr:2:numa:PE=9", 2, 6),
    )
    for variant in variants:
        # First smoke the toolchain, then compare all three physical-core layouts.
        for rpn, threads, mapping, p, q in ((1, 1, "ppr:1:node:PE=1", 1, 3), *layouts):
            smoke = rpn == 1
            n, nb = (4096, 128) if smoke else (32256, 256)
            case = args.output / f"{variant}-{rpn}x{threads}"
            case.mkdir()
            hosts = case / "hosts"
            hosts.write_text("".join(f"10.148.72.{i} slots={rpn}\n" for i in (1, 2, 3)))
            (case / "HPL.dat").write_text(HELPERS["write_input"](template, n, nb, p, q))
            libs = (
                "/opt/intel/oneapi/mkl/2026.1/lib:/opt/intel/oneapi/compiler/2026.1/lib"
            )
            values = {
                "HPL_BINARY": f"/shared/hpl/intel-builds/{variant}/install/bin/xhpl",
                "HPL_RANKS": rpn * 3,
                "OMP_NUM_THREADS": threads,
                "MPI_MAP_BY": mapping,
                "MPI_HOSTFILE": hosts.resolve(),
                "HPL_TIMEOUT_SECONDS": 90,
                "MPI_LIBRARY_PATH": libs
                + ":/shared/environment/view/lib:/shared/environment/view/lib64",
            }
            if variant.endswith("intelmpi"):
                mpi = "/opt/intel/oneapi/mpi/2021.18"
                ucx = subprocess.check_output(
                    [
                        "/shared/spack/bin/spack",
                        "-e",
                        "/shared/environment",
                        "location",
                        "-i",
                        "ucx",
                    ],
                    text=True,
                ).strip()
                values.update(
                    MPI_IMPLEMENTATION="intel",
                    MPI_LAUNCHER=mpi + "/bin/mpirun",
                    MPI_LIBRARY_PATH=libs
                    + ":"
                    + mpi
                    + "/lib:"
                    + mpi
                    + "/libfabric/lib:"
                    + ucx
                    + "/lib",
                    I_MPI_ROOT=mpi,
                    I_MPI_OFI_LIBRARY_INTERNAL=1,
                    FI_PROVIDER_PATH=mpi + "/libfabric/lib/prov",
                )
            settings = (
                base
                + "\n"
                + "\n".join(
                    f"export {k}={shlex.quote(str(v))}" for k, v in values.items()
                )
                + "\n"
            )
            (case / "settings.sh").write_text(settings)
            print(f"Running {case.name}: N={n}, grid={p}x{q}", flush=True)
            with (case / "launcher.log").open("w") as log:
                run = subprocess.run(
                    [
                        str(HERE / "hpl-eval.sh"),
                        str(case / "HPL.dat"),
                        str(case / "settings.sh"),
                        str(case / "run"),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            record = {
                "variant": variant,
                "directory": str((case / "run").resolve()),
                "exit_status": run.returncode,
                "layout": [rpn, threads, mapping],
                "n": n,
                "nb": nb,
                "p": p,
                "q": q,
                "pmap": 0,
            }
            if run.returncode == 0:
                record["result"] = json.loads((case / "run/result.json").read_text())
                print(f"Valid: {record['result']['gflops']} GFLOPS", flush=True)
            else:
                print(
                    f"Failed with status {run.returncode}: {case}/launcher.log",
                    flush=True,
                )
            records.append(record)
            (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
            valid = [r for r in records if "result" in r]
            if valid:
                (args.output / "best.json").write_text(
                    json.dumps(
                        max(valid, key=lambda r: r["result"]["gflops"]), indent=2
                    )
                )
            if smoke and run.returncode:
                break


if __name__ == "__main__":
    main()
