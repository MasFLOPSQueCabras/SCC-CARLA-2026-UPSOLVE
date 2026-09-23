#!/usr/bin/env python3
"""Validate single-case HPL inputs and complete, numerically valid output."""

import json
import math
import re
import sys
from pathlib import Path


def input_case(text: str) -> dict[str, int]:
    lines = text.splitlines()
    if len(lines) < 31:
        raise ValueError("HPL.dat must contain all 31 lines")
    # A submission is exactly one case; reject hidden parameter sweeps.
    for index in (4, 6, 9, 13, 15, 17, 19, 21, 23):
        if int(lines[index].split()[0]) != 1:
            raise ValueError("Expected exactly one HPL test case")
    if float(lines[12].split()[0]) != 16.0:
        raise ValueError("Residual threshold must be 16.0")
    case = dict(
        zip(
            ("n", "nb", "p", "q"),
            (int(lines[i].split()[0]) for i in (5, 7, 10, 11)),
            strict=True,
        )
    )
    if min(case.values()) < 1 or case["n"] < case["nb"]:
        raise ValueError("Invalid problem dimensions")
    return case


def result(output: str, input_text: str) -> dict:
    case = input_case(input_text)
    for pattern, count in (
        (r"(\d+) tests completed and passed residual checks", 1),
        (r"(\d+) tests completed and failed residual checks", 0),
        (r"(\d+) tests skipped because of illegal input values", 0),
    ):
        matches = re.findall(pattern, output)
        if matches != [str(count)]:
            raise ValueError("Incomplete or unsuccessful HPL numerical summary")
    rows = re.findall(
        r"^W\S+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$",
        output,
        re.MULTILINE,
    )
    if len(rows) != 1 or "End of Tests" not in output:
        raise ValueError("Expected one completed HPL performance row")
    row = rows[0]
    if list(map(int, row[:4])) != list(case.values()):
        raise ValueError("Output dimensions do not match the submitted input")
    seconds, gflops = map(float, row[4:])
    if not all(math.isfinite(x) and x > 0 for x in (seconds, gflops)):
        raise ValueError("HPL timing and GFLOPS must be finite and positive")
    residuals = re.findall(r"=\s*(\S+)\s+\.{2,}\s+PASSED", output)
    if len(residuals) != 1:
        raise ValueError("Missing unique passing residual")
    residual = float(residuals[0])
    if not math.isfinite(residual) or not 0 <= residual < 16:
        raise ValueError("Invalid residual")
    return case | {"seconds": seconds, "gflops": gflops, "residual": residual}


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("input", "result"):
        sys.exit("Usage: hpl-result.py input HPL.dat RANKS | result HPL.out HPL.dat")
    if sys.argv[1] == "input":
        case = input_case(Path(sys.argv[2]).read_text())
        if case["p"] * case["q"] != int(sys.argv[3]):
            sys.exit("P x Q must equal the launched rank count")
    else:
        print(
            json.dumps(
                result(Path(sys.argv[2]).read_text(), Path(sys.argv[3]).read_text()),
                indent=2,
            )
        )
