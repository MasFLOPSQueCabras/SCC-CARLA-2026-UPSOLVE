import socket
import subprocess
import time
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
            stop_cmd = (
                f"systemctl --user stop scc_http_{self.port} 2>/dev/null || true; "
                f"systemctl --user reset-failed scc_http_{self.port} 2>/dev/null || true"
            )
            subprocess.run(["ssh", self.bastion_ssh_host, stop_cmd], check=False)
            remote_cmd = (
                f"systemd-run --user --collect --unit=scc_http_{self.port} "
                f"sh -c 'cd ~/scc_serve && exec python3 -m RangeHTTPServer {self.port} --bind {self.bind_ip}'"
            )
            subprocess.run(
                ["ssh", self.bastion_ssh_host, remote_cmd],
                capture_output=True,
                text=True,
                check=True,
            )
        time.sleep(1)

    def stop(self) -> None:
        if self.local_proc:
            self.local_proc.kill()
            self.local_proc = None
        else:
            self.sweep_remote(self.bastion_ssh_host, self.port)

    @classmethod
    def sweep_remote(cls, bastion_ssh_host: str, port: int) -> None:
        cmd = (
            f"systemctl --user stop scc_http_{port} 2>/dev/null || true; "
            f"systemctl --user reset-failed scc_http_{port} 2>/dev/null || true; "
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
