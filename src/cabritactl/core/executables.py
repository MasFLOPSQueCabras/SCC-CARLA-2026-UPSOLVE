"""Find dependency executables next to the interpreter in isolated tool installs."""

import os
import shutil
import sys
from pathlib import Path


def find_executable(name: str) -> str | None:
    path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    return shutil.which(name, path=path)
