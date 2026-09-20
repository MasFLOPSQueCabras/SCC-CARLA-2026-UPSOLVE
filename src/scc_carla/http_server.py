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

from scc_carla.templating import TemplateEngine

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
        template_engine: TemplateEngine,
        remote_serve_dir: Path | str | None = None,
        serve_dir: Path | None = None,
    ) -> None:
        self.port = port
        self.bind_ip = bind_ip
        self.bastion_ssh_host = bastion_ssh_host
        self.bastion_hostname = bastion_hostname
        self.template_engine = template_engine
        self.remote_serve_dir = remote_serve_dir or (Path.home() / "scc_serve")
        self.serve_path = serve_dir or (Path.home() / "scc_serve")
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
        check_script = self.template_engine.render(
            "scripts/check_port.sh.j2",
            {"bind_ip": self.bind_ip, "port": self.port},
        )
        check_alive = subprocess.run(
            ["ssh", self.bastion_ssh_host, "bash -s"],
            input=check_script,
            text=True,
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
            "Starting ephemeral Range HTTP server on bastion %s:%s...",
            self.bind_ip,
            self.port,
        )

        server_script = self.template_engine.render(
            "scripts/bastion_http_server.sh.j2",
            {
                "bind_ip": self.bind_ip,
                "port": self.port,
                "serve_dir": str(self.remote_serve_dir),
            },
        )

        ssh_cmd = [
            "ssh",
            self.bastion_ssh_host,
            "bash -s",
        ]

        self._bastion_proc = subprocess.Popen(
            ssh_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if self._bastion_proc.stdin:
            self._bastion_proc.stdin.write(server_script)
            self._bastion_proc.stdin.close()

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
            raise RuntimeError(f"Failed to start Range HTTP server on bastion: {err}")

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
                logger.debug("Error stopping bastion HTTP server process: %s", err)
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
            stop_script = self.template_engine.render(
                "scripts/bastion_http_stop.sh.j2",
                {
                    "port": self.port,
                    "remove_dir": str(self.remote_serve_dir),
                },
            )
            subprocess.run(
                ["ssh", self.bastion_ssh_host, "bash -s"],
                input=stop_script,
                text=True,
                check=False,
                capture_output=True,
            )

        if self._local_server:
            try:
                self._local_server.shutdown()
                self._local_server.server_close()
            except OSError as err:
                logger.debug("Error shutting down local HTTP server: %s", err)
            self._local_server = None
            self._local_server_thread = None

        logger.info("Bastion HTTP server and staging cleanly stopped and removed.")

    @classmethod
    def sweep_remote(
        cls,
        bastion_ssh_host: str,
        port: int,
        template_engine: TemplateEngine,
        remote_dir: Path | str | None = None,
        force: bool = False,
    ) -> bool:
        """Kills any HTTP servers bound to port on the bastion and sweeps the staging directory if safe."""
        target_dir = str(remote_dir or (Path.home() / "scc_serve"))
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
                logger.debug("Could not verify node states before sweep: %s", e)

        stop_script = template_engine.render(
            "scripts/bastion_http_stop.sh.j2",
            {
                "port": port,
                "remove_dir": target_dir,
            },
        )
        subprocess.run(
            ["ssh", bastion_ssh_host, "bash -s"],
            input=stop_script,
            text=True,
            check=False,
            capture_output=True,
        )
        return True
