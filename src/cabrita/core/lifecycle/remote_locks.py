"""Bastion flock leases held by an SSH session, released on disconnect."""

import selectors
import shlex
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager


class BastionLocks:
    def __init__(self, host: str) -> None:
        if not host or host.startswith("-"):
            raise ValueError("Invalid bastion host")
        self.host = host

    @contextmanager
    def _acquire_one(self, key: str) -> Iterator[None]:
        if len(key) != 64 or any(
            character not in "0123456789abcdef" for character in key
        ):
            raise ValueError("Lock key must be a SHA-256 resource identity")
        command = (
            'mkdir -p "$HOME/.local/state/cabrita/locks" && flock -n "$HOME/.local/state/cabrita/locks/'
            + key
            + '.lock" sh -c '
            + shlex.quote("printf 'LOCKED\\n'; cat >/dev/null")
        )
        process = subprocess.Popen(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.host,
                command,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stdin is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if (
                    not selector.select(timeout=15)
                    or process.stdout.readline() != b"LOCKED\n"
                ):
                    raise RuntimeError(f"Cannot acquire bastion resource lock: {key}")
            yield
            if process.poll() is not None:
                raise RuntimeError(f"Bastion resource lock was lost: {key}")
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    @contextmanager
    def acquire(self, keys: list[str]) -> Iterator[None]:
        with ExitStack() as stack:
            for key in sorted(set(keys)):
                stack.enter_context(self._acquire_one(key))
            yield
