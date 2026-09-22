"""Fail a benchmark run unless HPL reports successful numerical checks."""

import re
import sys
from pathlib import Path


def validate(output: str) -> None:
    passed = re.search(r"(\d+) tests completed and passed residual checks", output)
    failed = re.search(r"(\d+) tests completed and failed residual checks", output)
    skipped = re.search(r"(\d+) tests skipped because of illegal input values", output)
    if (
        not passed
        or int(passed[1]) < 1
        or not failed
        or int(failed[1]) != 0
        or not skipped
        or int(skipped[1]) != 0
    ):
        raise ValueError("HPL did not complete all numerical checks successfully")


if __name__ == "__main__":
    validate(Path(sys.argv[1]).read_text())
    print("HPL numerical checks passed")
