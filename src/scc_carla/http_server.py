import functools
import http.server
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Self

from RangeHTTPServer import RangeRequestHandler

logger = logging.getLogger(__name__)


def is_running_on_bastion(bastion_hostname: str) -> bool:
    return socket.gethostname() == bastion_hostname


class EphemeralRangeHTTPServer:
    """Ephemeral HTTP Range server running directly on the bastion (or locally if executed on bastion).

    Serves ISO and OEMDRV images at high speeds to HPE iLO BMC over the internal network.
    Automatically starts the server on the bastion via SSH and tears it down on exit,
    sweeping any staging files and leaving zero lingering background processes or state.
    """

    def __init__(
        self,
        port: int,
        bind_ip: str,
        bastion_ssh_host: str,
        bastion_hostname: str,
        remote_serve_dir: str = "~/scc_serve",
        serve_dir: Path | None = None,
    ) -> None:
        self.port = port
        self.bind_ip = bind_ip
        self.bastion_ssh_host = bastion_ssh_host
        self.bastion_hostname = bastion_hostname
        self.remote_serve_dir = remote_serve_dir
        self.serve_path = serve_dir or Path.home() / "scc_serve"
        self.on_bastion = is_running_on_bastion(self.bastion_hostname)

        self._local_server: http.server.ThreadingHTTPServer | None = None
        self._local_server_thread: threading.Thread | None = None
        self._bastion_proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.stop()

    def start(self) -> None:
        if self.on_bastion:
            local_dir = Path(self.remote_serve_dir).expanduser()
            local_dir.mkdir(parents=True, exist_ok=True)
            handler = functools.partial(RangeRequestHandler, directory=str(local_dir))
            self._local_server = http.server.ThreadingHTTPServer(
                (self.bind_ip, self.port), handler
            )
            self._local_server_thread = threading.Thread(
                target=self._local_server.serve_forever, daemon=True
            )
            self._local_server_thread.start()
            logger.info(
                "Bastion Range server listening directly on %s:%s",
                self.bind_ip,
                self.port,
            )
            return

        # Running on local workstation: check if Range server is already active on bastion
        check_alive = subprocess.run(
            [
                "ssh",
                self.bastion_ssh_host,
                (
                    f"python3 -c \""
                    f"import socket; s = socket.socket(); s.settimeout(1.0); "
                    f"res = s.connect_ex(('{self.bind_ip}', {self.port})); s.close(); "
                    f"exit(0 if res == 0 else 1)\""
                ),
            ],
            check=False,
            capture_output=True,
        )
        if check_alive.returncode == 0:
            logger.info(
                "Active Range HTTP server detected on bastion %s:%s. Reusing existing instance.",
                self.bind_ip,
                self.port,
            )
            self._reused_existing = True
            return

        self._reused_existing = False
        logger.info(
            "Cleaning any stale HTTP servers on bastion port %s...", self.port
        )
        subprocess.run(
            [
                "ssh",
                self.bastion_ssh_host,
                (
                    f"fuser -k {self.port}/tcp 2>/dev/null || pkill -f 'RangeHTTPServer.*{self.port}' 2>/dev/null || true; "
                    f"mkdir -p {self.remote_serve_dir}"
                ),
            ],
            check=False,
            capture_output=True,
        )

        server_script = (
            f"import os, sys, http.server, functools; "
            f"from RangeHTTPServer import RangeRequestHandler; "
            f"path = os.path.expanduser('{self.remote_serve_dir}'); "
            f"os.makedirs(path, exist_ok=True); "
            f"os.chdir(path); "
            f"handler = functools.partial(RangeRequestHandler, directory=path); "
            f"server = http.server.ThreadingHTTPServer(('{self.bind_ip}', {self.port}), handler); "
            f"print('READY', flush=True); "
            f"server.serve_forever()"
        )

        ssh_cmd = [
            "ssh",
            self.bastion_ssh_host,
            f"python3 -c \"{server_script}\"",
        ]

        logger.info(
            "Starting ephemeral Range HTTP server on bastion %s:%s...",
            self.bind_ip,
            self.port,
        )
        self._bastion_proc = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for "READY" signal
        start_time = time.time()
        ready = False
        while time.time() - start_time < 15.0:
            if self._bastion_proc.stdout and self._bastion_proc.poll() is None:
                line = self._bastion_proc.stdout.readline()
                if "READY" in line:
                    ready = True
                    break
            time.sleep(0.1)

        if not ready:
            err = ""
            if self._bastion_proc.stderr:
                err = self._bastion_proc.stderr.read()
            self.stop()
            raise RuntimeError(
                f"Failed to start Range HTTP server on bastion: {err}"
            )

        logger.info(
            "Bastion Range HTTP server active and listening on %s:%s",
            self.bind_ip,
            self.port,
        )

    def stop(self) -> None:
        if getattr(self, "_reused_existing", False):
            logger.info(
                "Leaving active shared HTTP server running on bastion %s:%s",
                self.bind_ip,
                self.port,
            )
            return

        if self._bastion_proc:
            try:
                self._bastion_proc.terminate()
                self._bastion_proc.wait(timeout=3)
            except (OSError, subprocess.SubprocessError) as err:
                logger.debug(
                    "Error stopping bastion HTTP server process: %s", err
                )
            self._bastion_proc = None

        if not self.on_bastion:
            # Check if any cluster nodes are actively installing before tearing down remote server
            try:
                from scc_carla.config import get_settings
                from scc_carla.db import NodeLifecycle, get_all_nodes

                nodes = get_all_nodes(get_settings())
                if any(n.state == NodeLifecycle.INSTALLING for n in nodes):
                    logger.info(
                        "Node(s) currently installing; preserving bastion HTTP server and staging directory."
                    )
                    return
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not verify node installation state: %s", e)

            # Clean up processes and remove staging directory on bastion
            subprocess.run(
                [
                    "ssh",
                    self.bastion_ssh_host,
                    f"fuser -k {self.port}/tcp 2>/dev/null || true; rm -rf {self.remote_serve_dir}",
                ],
                check=False,
                capture_output=True,
            )

        if self._local_server:
            try:
                self._local_server.shutdown()
                self._local_server.server_close()
            except OSError as err:
                logger.debug(
                    "Error shutting down local HTTP server: %s", err
                )
            self._local_server = None
            self._local_server_thread = None

        logger.info(
            "Bastion HTTP server and staging cleanly stopped and removed."
        )

    @classmethod
    def sweep_remote(
        cls,
        bastion_ssh_host: str,
        port: int,
        remote_dir: str = "~/scc_serve",
        force: bool = False,
    ) -> bool:
        """Kills any HTTP servers bound to port on the bastion and sweeps the staging directory if safe."""
        if not force:
            try:
                from scc_carla.config import get_settings
                from scc_carla.db import NodeLifecycle, get_all_nodes

                nodes = get_all_nodes(get_settings())
                if any(n.state == NodeLifecycle.INSTALLING for n in nodes):
                    logger.warning(
                        "Preserving bastion HTTP server on port %s: one or more nodes are still in INSTALLING state.",
                        port,
                    )
                    return False
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not verify node installation state: %s", e)

        subprocess.run(
            [
                "ssh",
                bastion_ssh_host,
                (
                    f"fuser -k {port}/tcp 2>/dev/null || pkill -f 'RangeHTTPServer.*{port}' 2>/dev/null || true; "
                    f"rm -rf {remote_dir}"
                ),
            ],
            check=False,
            capture_output=True,
        )
        return True
