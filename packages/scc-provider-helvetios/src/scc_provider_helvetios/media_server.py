import functools
import http.server
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Self

from RangeHTTPServer import RangeRequestHandler
from scc_core.templating import TemplateEngine

logger = logging.getLogger(__name__)


def is_running_on_bastion(bastion_hostname: str) -> bool:
    return socket.gethostname() == bastion_hostname


class EphemeralRangeHTTPServer:
    """Ephemeral HTTP Range server running in user-space on the bastion.

    Serves ISO and OEMDRV images at high speeds to HPE iLO BMC over the internal network.
    Automatically starts the server on the bastion via SSH and tears it down on exit,
    leaving zero lingering background processes or state, and requiring zero sudo/root privileges.
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
        self._reused_existing: bool = False

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
                "Bastion Range server listening on %s:%s", self.bind_ip, self.port
            )
            return

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
        start_script = self.template_engine.render(
            "scripts/bastion_http_server.sh.j2",
            {
                "remote_serve_dir": str(self.remote_serve_dir),
                "port": self.port,
                "bind_ip": self.bind_ip,
            },
        )
        self._bastion_proc = subprocess.Popen(
            ["ssh", self.bastion_ssh_host, "bash -s"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if self._bastion_proc.stdin:
            self._bastion_proc.stdin.write(start_script)
            self._bastion_proc.stdin.close()

        for _ in range(15):
            time.sleep(0.3)
            verify_res = subprocess.run(
                ["ssh", self.bastion_ssh_host, "bash -s"],
                input=check_script,
                text=True,
                check=False,
                capture_output=True,
            )
            if verify_res.returncode == 0:
                logger.info(
                    "Remote ephemeral HTTP server online on bastion at %s:%s",
                    self.bind_ip,
                    self.port,
                )
                return

    def stop(self) -> None:
        if self.on_bastion:
            if self._local_server:
                self._local_server.shutdown()
                self._local_server.server_close()
            return

        if self._reused_existing:
            return

        if self._bastion_proc is not None:
            self._bastion_proc.terminate()
            try:
                self._bastion_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._bastion_proc.kill()
            self._bastion_proc = None

        stop_script = self.template_engine.render(
            "scripts/bastion_http_stop.sh.j2",
            {"port": self.port},
        )
        subprocess.run(
            ["ssh", self.bastion_ssh_host, "bash -s"],
            input=stop_script,
            text=True,
            check=False,
            capture_output=True,
        )
