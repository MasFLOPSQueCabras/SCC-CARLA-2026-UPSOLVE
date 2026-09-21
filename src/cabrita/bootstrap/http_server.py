"""A media server owned by one deployment session; never kills other listeners."""

import hashlib
import selectors
import shlex
import socket
import subprocess
import sys
from pathlib import Path
from types import TracebackType
from typing import Self

from cabrita.bootstrap import range_server


def is_running_on_bastion(bastion_hostname: str = "carlanga") -> bool:
    return socket.gethostname().lower() == bastion_hostname.lower()


class EphemeralRangeHTTPServer:
    def __init__(
        self,
        port: int,
        bind_ip: str,
        bastion_ssh_host: str,
        bastion_hostname: str,
        remote_serve_dir: Path | str | None = None,
    ) -> None:
        self.port = port
        self.bind_ip = bind_ip
        self.host = bastion_ssh_host
        self.local = is_running_on_bastion(bastion_hostname)
        self.directory = (
            str(remote_serve_dir) if remote_serve_dir is not None else "~/cabrita_serve"
        )
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        script = Path(range_server.__file__)
        if self.local:
            argv = [
                sys.executable,
                str(script),
                self.directory,
                self.bind_ip,
                str(self.port),
            ]
        else:
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            remote_script = f"/tmp/cabrita-range-server-{digest}.py"
            subprocess.run(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    str(script),
                    f"{self.host}:{remote_script}",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            argv = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.host,
                shlex.join(
                    [
                        "python3",
                        remote_script,
                        self.directory,
                        self.bind_ip,
                        str(self.port),
                    ]
                ),
            ]
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert self.process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if (
                    not selector.select(timeout=15)
                    or self.process.stdout.readline() != b"READY\n"
                ):
                    raise RuntimeError(
                        f"Media server did not start on {self.bind_ip}:{self.port}; check port ownership and storage permissions"
                    )
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        if self.process is None:
            return
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        assert self.process.stdout is not None
        self.process.stdout.close()
        self.process = None
