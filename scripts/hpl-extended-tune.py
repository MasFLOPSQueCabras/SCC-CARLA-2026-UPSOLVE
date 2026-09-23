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


def recover_cases(output, records, template):
    """Recover only actual completed runs; never synthesize an MPI exit status."""
    known = {r["directory"] for r in records}
    defaults = template.splitlines()
    for case in sorted(output.glob("case-*")):
        run = case / "run"
        if str(run.resolve()) in known:
            continue
        if not (run / "finished.txt").is_file():
            raise RuntimeError(f"Unfinished attempt requires inspection: {run}")
        values = {}
        for line in (run / "settings.sh").read_text().splitlines():
            line = line.removeprefix("export ")
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                words = shlex.split(value, comments=True)
                if words:
                    values[key] = words[0]
        source = (run / "HPL.dat").read_text()
        dimensions = HELPERS["HELPERS"]["input_case"](source)
        binary = values["HPL_BINARY"]
        variant = (
            binary.split("/intel-builds/")[1].split("/")[0]
            if "/intel-builds/" in binary
            else "openblas"
        )
        begun = dt.datetime.fromisoformat(
            (run / "metadata.txt").read_text().splitlines()[0]
        ).timestamp()
        finished = dt.datetime.fromisoformat(
            (run / "finished.txt").read_text().strip()
        ).timestamp()
        status = int((run / "exit-status.txt").read_text())
        record = {
            "directory": str(run.resolve()),
            "phase": json.loads((case / "plan.json").read_text()).get("phase", "screen")
            if (case / "plan.json").exists()
            else "screen",
            "variant": variant,
            "rpn": int(values["HPL_RANKS"]) // 3,
            "threads": int(values["OMP_NUM_THREADS"]),
            **dimensions,
            "parameters": {
                key: int(source.splitlines()[index].split()[0])
                for key, index in PARAMETERS.items()
                if int(source.splitlines()[index].split()[0])
                != int(defaults[index].split()[0])
            },
            "environment": json.loads((case / "plan.json").read_text()).get(
                "environment", {}
            )
            if (case / "plan.json").exists()
            else {},
            "exit_status": status,
            "wall_seconds": max(1, finished - begun),
            "wall_time_source": "metadata timestamps; initial startup excluded",
            "recovered": True,
        }
        if status == 0:
            record["result"] = HELPERS["HELPERS"]["result"](
                (run / "HPL.out").read_text(), source
            )
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--deadline", required=True, help="ISO 8601 UTC deadline")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--plan",
        type=Path,
        help="Trusted JSON with cases, variant settings, and validated seed records",
    )
    parser.add_argument("--screen-seconds", type=int, default=3000)
    parser.add_argument("--large-runs", type=int, choices=(1, 2), default=2)
    parser.add_argument(
        "--commit", help="Full tested Cabrita hash for the packaged repeat"
    )
    args = parser.parse_args()
    deadline = dt.datetime.fromisoformat(args.deadline).timestamp()
    start = time.time()
    if not 900 <= deadline - start <= 7200:
        parser.error("Deadline must be between 15 minutes and two hours away")
    if args.resume:
        session = json.loads((args.output / "session.json").read_text())
        if session["deadline_utc"] != args.deadline:
            parser.error("Resume must retain the original deadline")
        start = dt.datetime.fromisoformat(session["start_utc"]).timestamp()
    else:
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
    plan = json.loads(args.plan.read_text()) if args.plan else None
    if plan:
        for name, settings_path in plan.get("variants", {}).items():
            variants[name] = Path(settings_path).read_text()
        (args.output / "search-plan.json").write_text(json.dumps(plan, indent=2))
    records = (
        json.loads((args.output / "attempts.json").read_text()) if args.resume else []
    )
    if plan:
        known = {record["directory"] for record in records}
        for seed in plan.get("seed_records", []):
            if seed["directory"] in known:
                continue
            run = Path(seed["directory"])
            if (run / "exit-status.txt").read_text().strip() != "0":
                raise ValueError("Seed must be a completed successful run")
            seed["result"] = HELPERS["HELPERS"]["result"](
                (run / "HPL.out").read_text(), (run / "HPL.dat").read_text()
            )
            records.append(seed)
    if args.resume:
        records = recover_cases(args.output, records, template)
        (args.output / "attempts.json").write_text(json.dumps(records, indent=2))
    screen_end = min(start + args.screen_seconds, deadline - 1800)
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
        package=None,
        environment=None,
    ):
        parameters = parameters or {}
        environment = environment or {}
        if phase == "screen":
            for existing in records:
                if all(
                    existing.get(k, {} if k == "environment" else None) == v
                    for k, v in {
                        "phase": phase,
                        "variant": variant,
                        "rpn": rpn,
                        "threads": threads,
                        "p": p,
                        "q": q,
                        "nb": nb,
                        "n": n,
                        "parameters": parameters,
                        "environment": environment,
                    }.items()
                ):
                    return existing
        remaining = deadline - time.time() - 300
        if phase == "screen":
            remaining = min(remaining, screen_end - time.time())
        timeout = int(min(allowance, remaining))
        if timeout < (100 if phase == "screen" else 30):
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
            **environment,
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
        (case / "plan.json").write_text(
            json.dumps(
                {
                    "phase": phase,
                    "variant": variant,
                    "rpn": rpn,
                    "threads": threads,
                    "p": p,
                    "q": q,
                    "nb": nb,
                    "n": n,
                    "parameters": parameters,
                    "environment": environment,
                },
                indent=2,
            )
        )
        begun = time.time()
        with (case / "launcher.log").open("w") as log:
            command = (
                ["bash", str(package / "scripts/run-hpl.sh"), str(case / "run")]
                if package
                else [
                    "bash",
                    str(HERE / "hpl-eval.sh"),
                    str(case / "HPL.dat"),
                    str(case / "settings.sh"),
                    str(case / "run"),
                ]
            )
            completed = subprocess.run(
                command,
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
            "environment": environment,
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

    if plan:
        for case in plan["cases"]:
            execute(**case)
    else:
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
        # Intel oneMKL GEMM partitioning: compare against unchanged runtime controls.
        for variant in ("icx-mkl-openmpi", "icx-mkl-intelmpi"):
            screen(variant, 36, 1, 6, 18, 192)
        for environment in ({}, {"MKL_NUM_STRIPES": "1"}, {"MKL_NUM_STRIPES": "3"}):
            execute("icx-mkl-openmpi", 2, 18, 2, 3, 384, 73728, environment=environment)
        for stripes in ("1", "3"):
            execute(
                "icx-mkl-openmpi",
                6,
                6,
                3,
                6,
                256,
                73728,
                environment={"MKL_NUM_STRIPES": stripes},
            )
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
    if not good:
        raise SystemExit("No validated candidate for the large run")
    if plan and plan.get("large_candidate"):
        requested = plan["large_candidate"]
        preferred = [
            record
            for record in good
            if all(record.get(key) == value for key, value in requested.items())
        ]
        if not preferred:
            raise SystemExit("Requested large candidate has no validated measurement")
        good = preferred + [record for record in good if record not in preferred]
    # Prefer a memory-heavy candidate, with an optional packaged repeat.
    for index in range(args.large_runs):
        candidate = (
            good[0]
            if index == 0
            else max(
                (r for r in records if "result" in r),
                key=lambda r: r["result"]["gflops"],
            )
        )
        allowance = (deadline - time.time() - 300) / (args.large_runs - index)
        nb, p, q = candidate["nb"], candidate["p"], candidate["q"]
        alignment = nb * math.lcm(p, q)
        # Use the measured N=129024 baseline to avoid extrapolating only tiny cases.
        speed = baseline["n"] ** 3 / (baseline["seconds"] + 15)
        timed_n = int((allowance * 0.97 * speed) ** (1 / 3)) // alignment * alignment
        n = min(HELPERS["matrix_limit"](memory, 3, 0.88, nb, p, q), timed_n)
        package = None
        if index == 1:
            n = candidate["n"]
            if candidate["wall_seconds"] * 1.08 > allowance:
                print("Insufficient time to repeat best input safely", flush=True)
                break
            if args.commit:
                evidence = Path("/shared/hpl/build-evidence")
                if candidate["variant"] != "openblas":
                    evidence = args.output / "build-evidence"
                    subprocess.run(
                        [
                            "bash",
                            str(HERE / "hpl-oneapi-evidence.sh"),
                            candidate["variant"],
                            str(evidence),
                        ],
                        check=True,
                    )
                package = args.output / "replay-package"
                subprocess.run(
                    [
                        "python3",
                        str(HERE / "hpl-submit.py"),
                        candidate["directory"],
                        str(evidence),
                        str(package),
                        "--commit",
                        args.commit,
                        "--purpose",
                        "tuning",
                    ],
                    check=True,
                )
                subprocess.run(
                    ["sha256sum", "--check", "SHA256SUMS"], cwd=package, check=True
                )
        execute(
            candidate["variant"],
            candidate["rpn"],
            candidate["threads"],
            p,
            q,
            nb,
            max(n, candidate["n"]),
            candidate["parameters"],
            allowance=allowance,
            phase="repeat" if index else "large",
            package=package,
            environment=candidate.get("environment", {}),
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
