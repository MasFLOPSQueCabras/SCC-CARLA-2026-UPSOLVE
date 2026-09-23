#!/usr/bin/env python3
"""Continue the verified Helvetios search within an explicit UTC deadline."""

import argparse
import datetime as dt
import json
import math
import runpy
import shlex
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / "hpl-tune.py"))
PARAMETERS = {
    "pmap": 8,
    "pfact": 14,
    "nbmin": 16,
    "ndiv": 18,
    "rfact": 20,
    "bcast": 22,
    "depth": 24,
    "swap": 25,
    "swap_threshold": 26,
    "equil": 29,
}


def make_input(template, n, nb, p, q, parameters):
    lines = HELPERS["write_input"](template, n, nb, p, q).splitlines()
    for key, value in parameters.items():
        lines[PARAMETERS[key]] = str(value)
    result = "\n".join(lines) + "\n"
    HELPERS["HELPERS"]["input_case"](result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--deadline", required=True, help="ISO 8601 UTC deadline")
    args = parser.parse_args()
    deadline = dt.datetime.fromisoformat(args.deadline).timestamp()
    start = time.time()
    if not 900 <= deadline - start <= 7200:
        parser.error("Deadline must be between 15 minutes and two hours away")
    args.output.mkdir(parents=True, exist_ok=False)
    hosts = [f"10.148.72.{i}" for i in (1, 2, 3)]
    machines = [HELPERS["topology"](host) for host in hosts]
    (args.output / "topology.json").write_text(
        json.dumps(dict(zip(hosts, machines, strict=True)), indent=2)
    )
    memory = min(m["available"] for m in machines)
    if any(m["cores"] != 36 or m["numa"] != 2 for m in machines):
        raise SystemExit(
            "This search requires the verified 36-core, two-NUMA allocation"
        )
    template = Path("/shared/hpl/HPL.dat").read_text()
    variants = {"openblas": Path("/shared/hpl/hpl-settings.sh").read_text()}
    for name in ("gcc-mkl-openmpi", "icx-mkl-openmpi", "icx-mkl-intelmpi"):
        variants[name] = Path(
            f"/shared/hpl/oneapi-comparison/{name}-36x1/settings.sh"
        ).read_text()
    records = []
    screen_end = min(start + 2400, deadline - 4200)
    baseline = json.loads(
        Path("/shared/hpl/results/final-large/result.json").read_text()
    )
    (args.output / "session.json").write_text(
        json.dumps(
            {
                "start_utc": dt.datetime.fromtimestamp(start, dt.UTC).isoformat(),
                "deadline_utc": args.deadline,
                "previous_best": baseline,
                "screen_end_epoch": screen_end,
            },
            indent=2,
        )
    )

    def execute(
        variant,
        rpn,
        threads,
        p,
        q,
        nb,
        n,
        parameters=None,
        allowance=240,
        phase="screen",
    ):
        parameters = parameters or {}
        remaining = deadline - time.time() - 300
        if phase == "screen":
            remaining = min(remaining, screen_end - time.time())
        timeout = int(min(allowance, remaining))
        if timeout < 30:
            return None
        case = args.output / f"case-{len(records) + 1:03}"
        case.mkdir()
        hostfile = case / "hosts"
        hostfile.write_text("".join(f"{h} slots={rpn}\n" for h in hosts))
        mapping = f"ppr:{rpn // 2}:numa:PE={threads}"
        (case / "HPL.dat").write_text(make_input(template, n, nb, p, q, parameters))
        values = {
            "MPI_HOSTFILE": hostfile.resolve(),
            "HPL_RANKS": rpn * 3,
            "OMP_NUM_THREADS": threads,
            "MPI_MAP_BY": mapping,
            "HPL_TIMEOUT_SECONDS": timeout,
        }
        (case / "settings.sh").write_text(
            variants[variant]
            + "\n"
            + "\n".join(f"export {k}={shlex.quote(str(v))}" for k, v in values.items())
            + "\n"
        )
        print(
            f"{case.name}: phase={phase} variant={variant} ranks/node={rpn} threads={threads} N={n} NB={nb} grid={p}x{q} parameters={parameters} timeout={timeout}",
            flush=True,
        )
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
            "phase": phase,
            "variant": variant,
            "rpn": rpn,
            "threads": threads,
            "p": p,
            "q": q,
            "nb": nb,
            "n": n,
            "parameters": parameters,
            "exit_status": completed.returncode,
            "wall_seconds": time.time() - begun,
        }
        if completed.returncode == 0:
            record["result"] = HELPERS["HELPERS"]["result"](
                (case / "run/HPL.out").read_text(), (case / "HPL.dat").read_text()
            )
            print(
                f"PASS {record['result']['gflops']} GFLOPS, residual {record['result']['residual']}",
                flush=True,
            )
        else:
            print(
                f"FAILED exit={completed.returncode}; inspect {case}/launcher.log",
                flush=True,
            )
        records.append(record)
        (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
        good = [r for r in records if "result" in r]
        if good:
            (args.output / "best.json").write_text(
                json.dumps(max(good, key=lambda r: r["result"]["gflops"]), indent=2)
            )
        return record

    def screen(variant, rpn, threads, p, q, nb, parameters=None):
        alignment = nb * math.lcm(p, q)
        n = 73728 // alignment * alignment
        return execute(variant, rpn, threads, p, q, nb, n, parameters, allowance=240)

    # Larger screens expose communication and threading costs hidden at N=32k.
    for nb in (256, 384, 512, 192, 320, 640):
        for p, q in ((6, 18), (9, 12)):
            screen("openblas", 36, 1, p, q, nb)
    for variant in ("icx-mkl-openmpi", "icx-mkl-intelmpi"):
        for nb in (256, 384, 512):
            screen(variant, 36, 1, 6, 18, nb)
    for variant in ("openblas", "icx-mkl-openmpi", "icx-mkl-intelmpi"):
        for rpn, threads, p, q in ((18, 2, 6, 9), (12, 3, 6, 6), (6, 6, 3, 6)):
            screen(variant, rpn, threads, p, q, 256)
    good = sorted(
        (r for r in records if "result" in r),
        key=lambda r: r["result"]["gflops"],
        reverse=True,
    )
    if not good:
        raise SystemExit("No valid screen results")
    top = good[0]
    # Compare algorithmic alternatives on the strongest measured layout.
    changes = [
        {"bcast": 3},
        {"bcast": 4},
        {"bcast": 5},
        {"depth": 0},
        {"depth": 2},
        {"pfact": 1, "rfact": 2},
        {"nbmin": 8},
        {"ndiv": 4},
        {"pmap": 1},
        {"equil": 0},
    ]
    for parameters in changes:
        screen(
            top["variant"],
            top["rpn"],
            top["threads"],
            top["p"],
            top["q"],
            top["nb"],
            parameters,
        )
    good = sorted(
        (r for r in records if "result" in r),
        key=lambda r: r["result"]["gflops"],
        reverse=True,
    )
    # Two large candidates, then an exact repeat. Reserve five minutes for packaging.
    selected = good[:2]
    for index in range(3):
        current = max(
            (r for r in records if "result" in r), key=lambda r: r["result"]["gflops"]
        )
        candidate = selected[index] if index < 2 else current
        allowance = (deadline - time.time() - 300) / (3 - index)
        nb, p, q = candidate["nb"], candidate["p"], candidate["q"]
        alignment = nb * math.lcm(p, q)
        # Runtime model uses complete measured wall time and a 20% safety reserve.
        speed = candidate["n"] ** 3 / max(candidate["wall_seconds"], 1)
        timed_n = int((allowance * 0.8 * speed) ** (1 / 3)) // alignment * alignment
        n = min(HELPERS["matrix_limit"](memory, 3, 0.78, nb, p, q), timed_n)
        if index == 2:
            n = candidate["n"]
            if candidate["wall_seconds"] * 1.15 > allowance:
                print("Insufficient time to repeat best input safely", flush=True)
                break
        n = max(n, candidate["n"])
        execute(
            candidate["variant"],
            candidate["rpn"],
            candidate["threads"],
            p,
            q,
            nb,
            n,
            candidate["parameters"],
            allowance=allowance,
            phase="repeat" if index == 2 else "large",
        )
    (args.output / "completed.json").write_text(
        json.dumps(
            {
                "elapsed_seconds": time.time() - start,
                "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=2,
        )
    )
    print("Completed extended search", flush=True)


if __name__ == "__main__":
    main()
