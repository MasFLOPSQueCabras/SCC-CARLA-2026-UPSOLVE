#!/usr/bin/env python3
"""Bounded three-node HPL search. Execute on the installed head node."""

import argparse
import itertools
import json
import math
import runpy
import shlex
import subprocess
import time
from pathlib import Path

HELPERS = runpy.run_path(str(Path(__file__).with_name("hpl-result.py")))


def grids(ranks: int) -> list[tuple[int, int]]:
    return [(p, ranks // p) for p in range(math.isqrt(ranks), 0, -1) if ranks % p == 0][
        :2
    ]


def matrix_limit(
    memory: int, nodes: int, fraction: float, nb: int, p: int, q: int
) -> int:
    alignment = nb * math.lcm(p, q)
    # Alignment evenly distributes blocks; reserve 10% within the chosen budget
    # for panels, MPI buffers and BLAS workspace beyond the dense matrix.
    return int(math.sqrt(memory * nodes * fraction * 0.9 / 8)) // alignment * alignment


def write_input(template: str, n: int, nb: int, p: int, q: int) -> str:
    lines = template.splitlines()
    for index, value in ((5, n), (7, nb), (10, p), (11, q)):
        lines[index] = str(value)
    text = "\n".join(lines) + "\n"
    HELPERS["input_case"](text)
    return text


def topology(host: str) -> dict:
    program = """import json,subprocess
c=json.loads(subprocess.check_output(['lscpu','--json']))
f={x['field'].rstrip(':'):x['data'] for x in c['lscpu']}
m={x.split(':')[0]:x.split(':')[1].strip() for x in open('/proc/meminfo')}
print(json.dumps({'cores':int(f['Core(s) per socket'])*int(f['Socket(s)']),
'numa':int(f['NUMA node(s)']),'available':int(m['MemAvailable'].split()[0])*1024}))"""
    completed = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            shlex.join(["env", "LC_ALL=C", "python3", "-c", program]),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("settings", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--hosts", nargs=3, default=["node1", "node2", "node3"])
    parser.add_argument("--seconds", type=int, default=7200)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 7200:
        parser.error("Budget must be between 60 and 7200 seconds")
    start = time.monotonic()
    deadline = start + args.seconds
    args.output.mkdir(parents=True, exist_ok=False)
    template = args.input.read_text()
    HELPERS["input_case"](template)
    settings = args.settings.read_text()
    runner = Path(__file__).with_name("hpl-eval.sh").resolve()
    machines = [topology(host) for host in args.hosts]
    (args.output / "topology.json").write_text(
        json.dumps(dict(zip(args.hosts, machines, strict=True)), indent=2)
    )
    if len({(m["cores"], m["numa"]) for m in machines}) != 1:
        raise ValueError(
            "Heterogeneous CPU topology requires an explicit placement plan"
        )
    cores, numa = machines[0]["cores"], machines[0]["numa"]
    memory = min(m["available"] for m in machines)
    layouts = [(cores, 1, f"ppr:{cores}:node:PE=1")]
    for ranks_per_numa in (1, 2):
        ranks = numa * ranks_per_numa
        if cores % ranks == 0:
            threads = cores // ranks
            layout = (ranks, threads, f"ppr:{ranks_per_numa}:numa:PE={threads}")
            if layout not in layouts:
                layouts.append(layout)
    records = []
    attempt = 0

    def execute(layout, nb, p, q, n, allowance):
        nonlocal attempt
        remaining = deadline - time.monotonic()
        timeout = int(min(allowance, remaining - 20))
        if timeout < 10 or n < nb:
            return None
        attempt += 1
        case_dir = args.output / f"case-{attempt:03}"
        case_dir.mkdir()
        rpn, threads, placement = layout
        hosts = case_dir / "hosts"
        hosts.write_text("".join(f"{host} slots={rpn}\n" for host in args.hosts))
        dat = case_dir / "HPL.dat"
        dat.write_text(write_input(template, n, nb, p, q))
        env = case_dir / "settings.sh"
        env.write_text(
            settings
            + "\n"
            + "\n".join(
                f"{key}={shlex.quote(str(value))}"
                for key, value in {
                    "MPI_HOSTFILE": hosts.resolve(),
                    "HPL_RANKS": rpn * 3,
                    "OMP_NUM_THREADS": threads,
                    "MPI_MAP_BY": placement,
                    "HPL_TIMEOUT_SECONDS": timeout,
                }.items()
            )
            + "\n"
        )
        print(
            f"Run {attempt}: N={n}, NB={nb}, grid={p}x{q}, ranks/node={rpn}, threads={threads}, timeout={timeout}s",
            flush=True,
        )
        with (case_dir / "launcher.log").open("w") as log:
            process = subprocess.run(
                [
                    str(runner),
                    str(dat.resolve()),
                    str(env.resolve()),
                    str((case_dir / "run").resolve()),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        record = {
            "directory": str(case_dir.resolve()),
            "exit_status": process.returncode,
            "layout": layout,
            "nb": nb,
            "p": p,
            "q": q,
            "n": n,
        }
        if process.returncode == 0:
            record["result"] = HELPERS["result"](
                (case_dir / "run/HPL.out").read_text(), dat.read_text()
            )
            print(f"Valid: {record['result']['gflops']:.3f} GFLOPS", flush=True)
        records.append(record)
        (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
        valid = [r for r in records if "result" in r]
        if valid:
            winner = max(valid, key=lambda r: r["result"]["gflops"])
            (args.output / "best.json").write_text(json.dumps(winner, indent=2))
        return record

    screen_end = deadline - min(2700, args.seconds * 0.375)
    for layout, nb in itertools.product(layouts, (128, 192, 256, 384)):
        for p, q in grids(layout[0] * 3):
            if time.monotonic() >= screen_end - 30:
                break
            alignment = nb * math.lcm(p, q)
            n = min(
                32768 // alignment * alignment, matrix_limit(memory, 3, 0.1, nb, p, q)
            )
            execute(layout, nb, p, q, n, min(600, screen_end - time.monotonic() - 20))
    valid = sorted(
        (r for r in records if "result" in r),
        key=lambda r: r["result"]["gflops"],
        reverse=True,
    )
    if not valid:
        raise SystemExit("No valid HPL result; inspect attempt logs before continuing")
    # Two large candidates and a repeat. Size each to fit the remaining budget.
    for index, fraction in enumerate((0.7, 0.8, 0.8)):
        best = (
            valid[min(index, len(valid) - 1)]
            if index < 2
            else max(
                (r for r in records if "result" in r),
                key=lambda r: r["result"]["gflops"],
            )
        )
        nb, p, q = best["nb"], best["p"], best["q"]
        remaining_runs = 3 - index
        allowance = max(0, (deadline - time.monotonic() - 30) / remaining_runs)
        alignment = nb * math.lcm(p, q)
        # 2x safety margin against extrapolation from a shorter solve.
        timed_n = int((allowance * best["result"]["gflops"] * 1e9 * 0.75) ** (1 / 3))
        n = min(
            matrix_limit(memory, 3, fraction, nb, p, q),
            timed_n // alignment * alignment,
        )
        if index == 2:
            # Repeat the exact best input only when it fits the remaining time.
            if best["result"]["seconds"] * 1.5 > allowance:
                break
            n = best["n"]
        execute(tuple(best["layout"]), nb, p, q, n, allowance)
    (args.output / "elapsed-seconds.txt").write_text(
        f"{time.monotonic() - start:.3f}\n"
    )
    print(f"Best validated result: {args.output / 'best.json'}", flush=True)


if __name__ == "__main__":
    main()
