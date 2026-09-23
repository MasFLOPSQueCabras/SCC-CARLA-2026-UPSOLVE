#!/usr/bin/env python3
"""Compare panel settings, confirm a challenger, then run large OpenBLAS HPL."""

import argparse
import datetime as dt
import json
import runpy
import shlex
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    helpers = runpy.run_path(str(here / "hpl-tune.py"))
    reference = Path("/shared/hpl/extended-20260923T194450/case-035/run")
    template = (reference / "HPL.dat").read_text()
    settings = (reference / "settings.sh").read_text()
    records = []
    started = time.monotonic()
    (root / "session.json").write_text(
        json.dumps(
            {
                "started_utc": dt.datetime.now(dt.UTC).isoformat(),
                "large_n": 235008,
                "nb": 192,
                "grid": [6, 18],
                "large_timeout_seconds": 2400,
                "selection": "Require challenger repeat and mean > baseline by 1%; otherwise retain baseline",
            },
            indent=2,
        )
    )

    def execute(name, n, changes, timeout):
        case = root / name
        case.mkdir()
        lines = helpers["write_input"](template, n, 192, 6, 18).splitlines()
        for index, value in changes.items():
            lines[index] = str(value)
        dat = "\n".join(lines) + "\n"
        helpers["HELPERS"]["input_case"](dat)
        (case / "HPL.dat").write_text(dat)
        (case / "hosts").write_text(
            "".join(f"10.148.72.{i} slots=36\n" for i in (1, 2, 3))
        )
        values = {"MPI_HOSTFILE": case / "hosts", "HPL_TIMEOUT_SECONDS": timeout}
        (case / "settings.sh").write_text(
            settings
            + "\n"
            + "\n".join(f"export {k}={shlex.quote(str(v))}" for k, v in values.items())
            + "\n"
        )
        print(f"Starting {name}: N={n}, overrides={changes}", flush=True)
        with (case / "launcher.log").open("w") as log:
            proc = subprocess.run(
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
        record = {
            "directory": str(case / "run"),
            "changes": changes,
            "exit_status": proc.returncode,
        }
        if proc.returncode == 0:
            record["result"] = helpers["HELPERS"]["result"](
                (case / "run/HPL.out").read_text(), dat
            )
        records.append(record)
        (root / "attempts.json").write_text(json.dumps(records, indent=2))
        print(json.dumps(record), flush=True)
        return record

    baseline = execute("screen-baseline", 72576, {}, 180)
    if "result" not in baseline:
        raise RuntimeError("Baseline failed; inspect before continuing")
    candidates = [baseline]
    for name, changes in (("rfact2", {20: 2}), ("nbmin8", {16: 8}), ("ndiv3", {18: 3})):
        record = execute("screen-" + name, 72576, changes, 180)
        if "result" in record:
            candidates.append(record)
    selected = max(candidates, key=lambda r: r["result"]["gflops"])
    if selected is not baseline:
        repeat = execute("screen-confirm", 72576, selected["changes"], 180)
        if (
            "result" not in repeat
            or (repeat["result"]["gflops"] + selected["result"]["gflops"]) / 2
            <= baseline["result"]["gflops"] * 1.01
        ):
            selected = baseline
    (root / "selection.json").write_text(json.dumps(selected, indent=2))
    machines = {host: helpers["topology"](host) for host in ("node1", "node2", "node3")}
    (root / "memory-before-large.json").write_text(json.dumps(machines, indent=2))
    required = 235008**2 * 8 / 3
    if min(m["available"] for m in machines.values()) < required / 0.8:
        raise RuntimeError("Insufficient memory headroom for planned large case")
    monitors = []
    try:
        for host in machines:
            log = (root / f"turbostat-{host}.txt").open("w")
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                host,
                "sudo",
                "-n",
                "timeout",
                "--kill-after=5",
                "2430",
                "turbostat",
                "--quiet",
                "--Summary",
                "--interval",
                "15",
                "--num_iterations",
                "160",
            ]
            monitors.append(
                (subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT), log)
            )
        execute("large", 235008, selected["changes"], 2400)
    finally:
        for monitor, log in monitors:
            monitor.terminate()
            try:
                monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                monitor.kill()
                monitor.wait()
            log.close()
        (root / "completed.json").write_text(
            json.dumps(
                {
                    "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "elapsed_seconds": time.monotonic() - started,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
