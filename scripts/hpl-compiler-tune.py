#!/usr/bin/env python3
"""Use remaining tuning time for an independently built Intel compiler variant."""

import argparse
import datetime as dt
import json
import runpy
import shlex
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / "hpl-tune.py"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--deadline", required=True)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument(
        "--variant",
        choices=(
            "icx-mkl-intelmpi-fast",
            "icx-mkl-intelmpi-mixed",
            "icx-openblas-openmpi-mixed",
        ),
        default="icx-mkl-intelmpi-mixed",
    )
    parser.add_argument("--screen-only", action="store_true")
    args = parser.parse_args()
    deadline = dt.datetime.fromisoformat(args.deadline).timestamp()
    args.output.mkdir(parents=True, exist_ok=False)
    while not args.after.is_file():
        if deadline - time.time() < 480:
            raise SystemExit("Insufficient remaining time; no additional build started")
        time.sleep(10)
    variant = args.variant
    with (args.output / "build-launcher.log").open("w") as log:
        subprocess.run(
            [
                "timeout",
                "--signal=TERM",
                "--kill-after=5",
                "120",
                "bash",
                str(HERE / "hpl-build-oneapi.sh"),
                variant,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=130,
        )
    base_variant = "icx-mkl-openmpi" if "openmpi" in variant else "icx-mkl-intelmpi"
    base = Path(
        f"/shared/hpl/oneapi-comparison/{base_variant}-36x1/settings.sh"
    ).read_text()
    template = Path("/shared/hpl/HPL.dat").read_text()
    records = []

    def execute(name, n, nb, allowance):
        case = args.output / name
        case.mkdir()
        hosts = case / "hosts"
        hosts.write_text("".join(f"10.148.72.{i} slots=36\n" for i in (1, 2, 3)))
        values = {
            "HPL_BINARY": f"/shared/hpl/intel-builds/{variant}/install/bin/xhpl",
            "MPI_HOSTFILE": hosts.resolve(),
            "HPL_RANKS": 108,
            "OMP_NUM_THREADS": 1,
            "MPI_MAP_BY": "ppr:18:numa:PE=1",
            "HPL_TIMEOUT_SECONDS": int(allowance),
        }
        (case / "settings.sh").write_text(
            base
            + "\n"
            + "\n".join(f"export {k}={shlex.quote(str(v))}" for k, v in values.items())
            + "\n"
        )
        (case / "HPL.dat").write_text(HELPERS["write_input"](template, n, nb, 6, 18))
        print(f"Running {name}: N={n} NB={nb} timeout={int(allowance)}", flush=True)
        begun = time.time()
        with (case / "launcher.log").open("w") as log:
            completed = subprocess.run(
                [
                    "bash",
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
            "directory": str((case / "run").resolve()),
            "variant": variant,
            "exit_status": completed.returncode,
            "wall_seconds": time.time() - begun,
        }
        if completed.returncode == 0:
            record["result"] = HELPERS["HELPERS"]["result"](
                (case / "run/HPL.out").read_text(), (case / "HPL.dat").read_text()
            )
        records.append(record)
        (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
        print(json.dumps(record), flush=True)
        return record

    smoke = execute("smoke", 6912, 192, min(90, deadline - time.time() - 60))
    if "result" not in smoke:
        raise SystemExit("Compiler variant failed smoke validation")
    screen = execute("screen", 72576, 192, min(180, deadline - time.time() - 60))
    if "result" not in screen:
        raise SystemExit("Compiler variant failed screen validation")
    allowance = min(900, deadline - time.time() - 90)
    if allowance >= 150 and not args.screen_only:
        # Reserve 15% of estimated solve time and 30 seconds of launch overhead.
        speed = screen["result"]["gflops"] * 1e9
        n = int(((allowance - 30) * speed * 1.5 * 0.85) ** (1 / 3)) // 3456 * 3456
        execute("large", min(155520, n), 192, allowance)
    (args.output / "completed.json").write_text(
        json.dumps(
            {
                "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
                "deadline_utc": args.deadline,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
