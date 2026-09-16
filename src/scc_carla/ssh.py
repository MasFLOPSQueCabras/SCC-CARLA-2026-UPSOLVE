import hashlib
import logging
import socket
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Self

from scc_carla.http_server import is_running_on_bastion

logger = logging.getLogger(__name__)


class SSHSession:
    """Manages an OpenSSH multiplexed session (ControlMaster) to the bastion.

    When running locally, opens a master control socket so subsequent SSH and SCP
    commands reuse the same authenticated TCP connection, reducing latency from ~800ms
    to <20ms per command.
    """

    def __init__(self, bastion_ssh_host: str, bastion_hostname: str) -> None:
        self.bastion_ssh_host = bastion_ssh_host
        self.bastion_hostname = bastion_hostname
        self.on_bastion = is_running_on_bastion(bastion_hostname)

        host_hash = hashlib.sha256(bastion_ssh_host.encode("utf-8")).hexdigest()[:8]
        self.control_path = f"/tmp/scc_ssh_mux_{host_hash}"
        self._master_proc: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 10.0) -> None:
        if self.on_bastion:
            return

        if self._is_master_alive():
            return

        Path(self.control_path).unlink(missing_ok=True)
        cmd = [
            "ssh",
            "-o",
            "ControlMaster=yes",
            "-o",
            f"ControlPath={self.control_path}",
            "-o",
            "ControlPersist=600s",
            "-N",
            "-f",
            self.bastion_ssh_host,
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        start = time.time()
        while time.time() - start < timeout:
            if self._is_master_alive():
                return
            time.sleep(0.1)

        logger.warning("SSH ControlMaster socket did not appear within %ss", timeout)

    def stop(self) -> None:
        if self.on_bastion:
            return

        if self._is_master_alive():
            cmd = [
                "ssh",
                "-o",
                f"ControlPath={self.control_path}",
                "-O",
                "exit",
                self.bastion_ssh_host,
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        Path(self.control_path).unlink(missing_ok=True)

    def _is_master_alive(self) -> bool:
        if not Path(self.control_path).exists():
            return False
        cmd = [
            "ssh",
            "-o",
            f"ControlPath={self.control_path}",
            "-O",
            "check",
            self.bastion_ssh_host,
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0

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

    def run(
        self,
        cmd: str,
        timeout: int = 60,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Runs a command on the bastion (or locally if already running on bastion)."""
        if self.on_bastion:
            return subprocess.run(
                cmd,
                shell=True,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                check=check,
            )

        ssh_cmd = ["ssh"]
        if Path(self.control_path).exists():
            ssh_cmd.extend(["-o", f"ControlPath={self.control_path}"])
        ssh_cmd.extend([self.bastion_ssh_host, cmd])

        return subprocess.run(
            ssh_cmd,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            check=check,
        )

    def scp_to(
        self, local_paths: list[Path], remote_dir: str, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        """Copies local files to remote directory on the bastion."""
        if self.on_bastion:
            dst = Path(remote_dir).expanduser()
            dst.mkdir(parents=True, exist_ok=True)
            for p in local_paths:
                cmd = f"cp '{p}' '{dst}/'"
                subprocess.run(cmd, shell=True, check=True)
            return subprocess.CompletedProcess(args=[], returncode=0)

        scp_cmd = ["scp"]
        if Path(self.control_path).exists():
            scp_cmd.extend(["-o", f"ControlPath={self.control_path}"])
        scp_cmd.extend([str(p) for p in local_paths])
        scp_cmd.append(f"{self.bastion_ssh_host}:{remote_dir}")

        return subprocess.run(
            scp_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )

    def is_port_open(self, target_ip: str, port: int = 22, timeout: int = 2) -> bool:
        """Probes TCP port connectivity on target IP from bastion vantage point."""
        if self.on_bastion:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((target_ip, port)) == 0

        probe_cmd = f"nc -z -w {timeout} {target_ip} {port}"
        res = self.run(probe_cmd, timeout=timeout + 3, check=False)
        return res.returncode == 0
