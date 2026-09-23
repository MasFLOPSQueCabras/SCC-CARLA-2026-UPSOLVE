#!/usr/bin/env python3
"""Build and compare single-thread BLIS after another benchmark completes."""

import argparse
import datetime as dt
import json
import runpy
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--deadline", required=True)
    args = parser.parse_args()
    end = dt.datetime.fromisoformat(args.deadline).timestamp()
    here = Path(__file__).resolve().parent
    helpers = runpy.run_path(str(here / "hpl-tune.py"))
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    try:
        while not args.after.exists():
            if end - time.time() < 150:
                raise RuntimeError("Insufficient time for BLIS build and checks")
            time.sleep(5)
        allowance = min(180, int(end - time.time() - 100))
        if allowance < 30:
            raise RuntimeError("Insufficient time for BLIS build")
        if not Path("/shared/hpl/blis-build/install/bin/xhpl").exists():
            subprocess.run(
                [
                    "timeout",
                    "--kill-after=5",
                    str(allowance),
                    "bash",
                    str(here / "hpl-build-blis.sh"),
                ],
                check=True,
            )
        for name, n in (("smoke", 6912), ("screen", 72576)):
            allowance = min(120, int(end - time.time() - 15))
            if allowance < 20:
                raise RuntimeError("Insufficient time for next BLIS check")
            case = args.output / name
            case.mkdir()
            template = Path("/shared/hpl/HPL.dat").read_text()
            (case / "HPL.dat").write_text(
                helpers["write_input"](template, n, 192, 6, 18)
            )
            (case / "hosts").write_text(
                "".join(f"10.148.72.{i} slots=36\n" for i in (1, 2, 3))
            )
            base = Path("/shared/hpl/hpl-settings.sh").read_text()
            (case / "settings.sh").write_text(
                base + f"\nexport HPL_BINARY=/shared/hpl/blis-build/install/bin/xhpl\n"
                f"export MPI_HOSTFILE={case.resolve()}/hosts\n"
                "export HPL_RANKS=108\nexport OMP_NUM_THREADS=1\n"
                "export MPI_MAP_BY=ppr:18:numa:PE=1\n"
                f"export HPL_TIMEOUT_SECONDS={allowance}\n"
            )
            print(f"BLIS {name}: N={n} NB=192 108 ranks", flush=True)
            with (case / "launcher.log").open("w") as log:
                run = subprocess.run(
                    [
                        "bash",
                        str(here / "hpl-eval.sh"),
                        str(case / "HPL.dat"),
                        str(case / "settings.sh"),
                        str(case / "run"),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            record = {"directory": str(case / "run"), "exit_status": run.returncode}
            if run.returncode == 0:
                record["result"] = json.loads((case / "run/result.json").read_text())
            records.append(record)
            (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
            print(json.dumps(record), flush=True)
            if run.returncode:
                raise RuntimeError("BLIS run failed; inspect preserved logs")
    finally:
        (args.output / "completed.json").write_text(
            json.dumps(
                {
                    "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "attempts": records,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
