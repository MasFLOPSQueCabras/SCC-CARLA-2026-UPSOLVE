"""Immutable checksum-addressed artifacts, published only after verification."""

import fcntl
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cabrita.core.bootstrap import ArtifactSpec


@contextmanager
def artifact_lock(path: Path, timeout: float = 1800) -> Iterator[None]:
    deadline = time.monotonic() + timeout
    with path.open("a+") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Artifact is locked: {path}") from None
                time.sleep(0.1)
        yield


class ArtifactCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def materialize(self, artifact: ArtifactSpec) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{artifact.sha256.lower()}.{artifact.format}"
        with artifact_lock(target.with_suffix(".lock")):
            if target.exists():
                artifact.verify(target)
                return target
            partial = target.with_suffix(target.suffix + ".partial")
            if artifact.source.startswith(("https://", "http://")):
                subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--retry",
                        "3",
                        "--max-time",
                        "1800",
                        "--continue-at",
                        "-",
                        "--output",
                        str(partial),
                        artifact.source,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=1900,
                )
            else:
                shutil.copyfile(artifact.source, partial)
            try:
                artifact.verify(partial)
            except ValueError:
                partial.unlink(missing_ok=True)
                raise
            partial.chmod(0o644)
            partial.replace(target)
        return target
