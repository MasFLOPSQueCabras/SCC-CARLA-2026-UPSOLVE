import socket
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Self


def is_running_on_bastion(bastion_hostname: str) -> bool:
    return socket.gethostname() == bastion_hostname


class EphemeralRangeHTTPServer:
    def __init__(
        self,
        port: int,
        bind_ip: str,
        bastion_ssh_host: str,
        bastion_hostname: str,
        serve_dir: Path | None = None,
    ) -> None:
        self.port = port
        self.bind_ip = bind_ip
        self.bastion_ssh_host = bastion_ssh_host
        self.bastion_hostname = bastion_hostname
        self.serve_path = (
            serve_dir if serve_dir is not None else (Path.home() / "scc_serve")
        )
        self.on_bastion = is_running_on_bastion(self.bastion_hostname)
        self.local_proc: subprocess.Popen[bytes] | None = None
        self.remote_pid: int | None = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.stop()

    def start(self) -> None:
        if self.on_bastion:
            self.serve_path.mkdir(parents=True, exist_ok=True)
            self.local_proc = subprocess.Popen(
                [
                    "python3",
                    "-m",
                    "RangeHTTPServer",
                    str(self.port),
                    "--bind",
                    self.bind_ip,
                ],
                cwd=self.serve_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            remote_cmd = (
                f"mkdir -p {self.serve_path} && cd {self.serve_path} && "
                f"nohup python3 -m RangeHTTPServer {self.port} --bind {self.bind_ip} >/dev/null 2>&1 & echo $!"
            )
            res = subprocess.run(
                ["ssh", self.bastion_ssh_host, remote_cmd],
                capture_output=True,
                text=True,
                check=True,
            )
            pid_str = res.stdout.strip()
            if pid_str.isdigit():
                self.remote_pid = int(pid_str)
                subprocess.run(
                    [
                        "ssh",
                        self.bastion_ssh_host,
                        f"echo {self.remote_pid} > ~/.scc_http_{self.port}.pid",
                    ],
                    check=False,
                )
        time.sleep(1)

    def stop(self) -> None:
        if self.local_proc:
            self.local_proc.kill()
            self.local_proc = None
        else:
            self.sweep_remote(self.bastion_ssh_host, self.port)
            self.remote_pid = None

    @classmethod
    def sweep_remote(cls, bastion_ssh_host: str, port: int) -> None:
        cmd = (
            f"if [ -f ~/.scc_http_{port}.pid ]; then kill $(cat ~/.scc_http_{port}.pid) 2>/dev/null || true; rm -f ~/.scc_http_{port}.pid; fi; "
            f"pkill -f 'RangeHTTPServer {port}' 2>/dev/null || true"
        )
        try:
            subprocess.run(
                ["ssh", bastion_ssh_host, cmd],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except subprocess.SubprocessError, OSError:
            return


@contextmanager
def ephemeral_http_server(
    port: int,
    bind_ip: str,
    bastion_ssh_host: str,
    bastion_hostname: str,
    serve_dir: Path | None = None,
) -> Generator[EphemeralRangeHTTPServer]:
    srv = EphemeralRangeHTTPServer(
        port=port,
        bind_ip=bind_ip,
        bastion_ssh_host=bastion_ssh_host,
        bastion_hostname=bastion_hostname,
        serve_dir=serve_dir,
    )
    try:
        srv.start()
        yield srv
    finally:
        srv.stop()
