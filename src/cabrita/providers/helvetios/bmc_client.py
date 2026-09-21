import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2

from cabrita.core.manifest.models import ClusterManifest

logger = logging.getLogger(__name__)


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SSHSocksTunnel:
    """Manages an ephemeral SSH SOCKS5 tunnel to the bastion host."""

    def __init__(self, bastion_ssh_host: str, local_port: int = 10872) -> None:
        self.bastion_ssh_host = bastion_ssh_host
        self.local_port = local_port
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 3.0) -> None:
        if self._is_port_open():
            self.local_port = _find_free_local_port()

        self.process = subprocess.Popen(
            [
                "ssh",
                "-o",
                "ConnectTimeout=2",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-D",
                str(self.local_port),
                "-N",
                self.bastion_ssh_host,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        start = time.time()
        while time.time() - start < timeout:
            if self._is_port_open():
                return
            if self.process.poll() is not None:
                raise ConnectionError(
                    f"SSH tunnel to {self.bastion_ssh_host} exited prematurely with code {self.process.returncode}"
                )
            time.sleep(0.2)

        self.stop()
        raise TimeoutError(
            f"Failed to establish SSH SOCKS tunnel on 127.0.0.1:{self.local_port} within {timeout}s"
        )

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError) as err:
                logger.debug("Failed graceful termination of SSH tunnel: %s", err)
                self.process.kill()
            finally:
                self.process = None

    def _is_port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", self.local_port)) == 0

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


class RedfishClient:
    """Client for HPE iLO 5 Redfish REST API."""

    def __init__(
        self,
        bmc_ip: str,
        bmc_user: str = "",
        bmc_password: str = "",
        proxy_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.bmc_ip = bmc_ip
        self.bmc_user = bmc_user
        self.bmc_password = bmc_password
        self.base_url = f"https://{bmc_ip}"
        self.timeout = timeout
        self.session_location: str | None = None
        self.auth_token: str | None = None

        self._client = httpx2.Client(
            proxy=proxy_url,
            verify=False,
            timeout=self.timeout,
        )

    def login(self) -> bool:
        if not self.bmc_user or not self.bmc_password:
            return False

        url = f"{self.base_url}/redfish/v1/SessionService/Sessions/"
        payload = {
            "UserName": self.bmc_user,
            "Password": self.bmc_password,
        }
        try:
            resp = self._client.post(url, json=payload)
            if resp.status_code in (200, 201):
                self.auth_token = resp.headers.get("X-Auth-Token")
                self.session_location = resp.headers.get("Location")
                return True
        except httpx2.HTTPError as err:
            logger.debug("Failed Redfish login on %s: %s", self.bmc_ip, err)
        return False

    def logout(self) -> None:
        if self.session_location and self.auth_token:
            url = (
                self.session_location
                if self.session_location.startswith("http")
                else f"{self.base_url}{self.session_location}"
            )
            try:
                self._client.delete(url, headers={"X-Auth-Token": self.auth_token})
            except httpx2.HTTPError as err:
                logger.debug("Failed Redfish logout on %s: %s", self.bmc_ip, err)
            self.session_location = None
            self.auth_token = None

    def close(self) -> None:
        self.logout()
        self._client.close()

    def __enter__(self) -> Self:
        self.login()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["X-Auth-Token"] = self.auth_token
        return headers

    def get(self, path: str) -> httpx2.Response:
        url = f"{self.base_url}{path}"
        return self._client.get(url, headers=self._headers())

    def post(
        self, path: str, json_data: dict[str, Any] | None = None
    ) -> httpx2.Response:
        url = f"{self.base_url}{path}"
        return self._client.post(url, headers=self._headers(), json=json_data or {})

    def patch(self, path: str, json_data: dict[str, Any]) -> httpx2.Response:
        url = f"{self.base_url}{path}"
        return self._client.patch(url, headers=self._headers(), json=json_data)


class BMCController:
    """Orchestrator for baremetal node management via Redfish."""

    def __init__(
        self,
        manifest: ClusterManifest | Any | None = None,
        bastion_ssh_host: str = "cabrita-bastion",
        bastion_hostname: str = "carlanga",
        bmc_user: str = "",
        bmc_password: str = "",
    ) -> None:
        match manifest:
            case ClusterManifest() | None:
                self.manifest = manifest
                self.settings = None
                self.bastion_ssh_host = (
                    manifest.bastion.ssh_host if manifest else bastion_ssh_host
                )
                self.bastion_hostname = bastion_hostname
                self.default_bmc_user = bmc_user
                self.default_bmc_password = bmc_password
            case settings:
                # ClusterSettings compatibility
                self.manifest = None
                self.settings = settings
                self.bastion_ssh_host = getattr(
                    settings, "bastion_ssh_host", bastion_ssh_host
                )
                self.bastion_hostname = getattr(
                    settings, "bastion_hostname", bastion_hostname
                )
                self.default_bmc_user = getattr(settings, "bmc_user", bmc_user)
                self.default_bmc_password = getattr(
                    settings, "bmc_password", bmc_password
                )
        self.socks_port = 10872
        self._tunnel: SSHSocksTunnel | None = None

    def _is_on_bastion(self) -> bool:
        return socket.gethostname() == self.bastion_hostname

    def _ensure_transport(self) -> str | None:
        if self._is_on_bastion():
            return None
        if getattr(self, "_transport_failed", False):
            raise ConnectionError(
                f"Bastion transport to {self.bastion_ssh_host} is unavailable."
            )
        if self._tunnel is None:
            self._tunnel = SSHSocksTunnel(
                bastion_ssh_host=self.bastion_ssh_host,
                local_port=self.socks_port,
            )
            try:
                self._tunnel.start()
            except Exception:
                self._transport_failed = True
                self._tunnel = None
                raise
        return f"socks5://127.0.0.1:{self._tunnel.local_port}"

    def close(self) -> None:
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _get_node_bmc_info(self, node_id: int) -> tuple[str, str, str]:
        if self.manifest:
            for n in self.manifest.nodes:
                if n.id == node_id and n.bmc:
                    return (
                        n.bmc.ip,
                        n.bmc.user or self.default_bmc_user,
                        n.bmc.password or self.default_bmc_password,
                    )
        if self.settings is not None:
            return (
                self.settings.get_bmc_ip(node_id),
                self.settings.bmc_user,
                self.settings.bmc_password,
            )
        # Default fallback formula
        return (f"10.1.72.{node_id}", self.default_bmc_user, self.default_bmc_password)

    def get_client(self, node_id: int, timeout: float = 30.0) -> RedfishClient:
        proxy_url = self._ensure_transport()
        bmc_ip, user, pw = self._get_node_bmc_info(node_id)
        return RedfishClient(
            bmc_ip=bmc_ip,
            bmc_user=user,
            bmc_password=pw,
            proxy_url=proxy_url,
            timeout=timeout,
        )

    def get_power_status(self, node_id: int, timeout: float = 2.0) -> str:
        if getattr(self, "_unreachable", False):
            return "UNKNOWN"
        try:
            with self.get_client(node_id, timeout=timeout) as client:
                resp = client.get("/redfish/v1/Systems/1/")
                if resp.status_code == 200:
                    power = resp.json().get("PowerState", "").upper()
                    if power in ("ON", "OFF"):
                        return power
        except (httpx2.HTTPError, OSError) as err:
            self._unreachable = True
            logger.debug("Failed power status on Node %s: %s", node_id, err)
        return "UNKNOWN"

    def power_off(self, node_id: int, graceful: bool = False) -> bool:
        reset_type = "GracefulShutdown" if graceful else "ForceOff"
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                if resp.status_code in (200, 204) or "Power is off" in resp.text:
                    return True
                if graceful:
                    resp = client.post(
                        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                        {"ResetType": "ForceOff"},
                    )
                    return resp.status_code in (200, 204) or "Power is off" in resp.text
        except httpx2.HTTPError, OSError:
            pass
        return False

    def power_on(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": "On"},
                )
                return resp.status_code in (200, 204) or "Power is on" in resp.text
        except httpx2.HTTPError, OSError:
            return False

    def reset(self, node_id: int, graceful: bool = False) -> bool:
        reset_type = "GracefulRestart" if graceful else "ForceRestart"
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                return resp.status_code in (200, 204)
        except httpx2.HTTPError, OSError:
            return False

    def eject_virtual_media(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                for slot in ("2", "1"):
                    client.post(
                        f"/redfish/v1/Managers/1/VirtualMedia/{slot}/Actions/VirtualMedia.EjectMedia/"
                    )
                return True
        except httpx2.HTTPError, OSError:
            return False

    def mount_and_boot(self, node_id: int, iso_url: str, floppy_url: str = "") -> bool:
        try:
            with self.get_client(node_id) as client:
                self.power_off(node_id, graceful=False)
                time.sleep(1.0)
                self.eject_virtual_media(node_id)

                # Mount CD / ISO in slot 2
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia/",
                    {"Image": iso_url},
                )
                # Mount Floppy in slot 1 if provided
                if floppy_url:
                    client.post(
                        "/redfish/v1/Managers/1/VirtualMedia/1/Actions/VirtualMedia.InsertMedia/",
                        {"Image": floppy_url},
                    )

                # Set one-time boot to CD
                client.patch(
                    "/redfish/v1/Systems/1/",
                    {
                        "Boot": {
                            "BootSourceOverrideTarget": "Cd",
                            "BootSourceOverrideEnabled": "Once",
                        }
                    },
                )
                self.power_on(node_id)
                return True
        except (httpx2.HTTPError, OSError) as err:
            logger.error("Failed virtual media mount on Node %s: %s", node_id, err)
            return False

    def get_power_metrics(self, node_id: int) -> dict[str, Any] | None:
        try:
            with self.get_client(node_id) as client:
                resp = client.get("/redfish/v1/Chassis/1/Power/")
                if resp.status_code == 200:
                    power_ctl = resp.json().get("PowerControl", [])
                    if power_ctl:
                        pc = power_ctl[0]
                        metrics = pc.get("PowerMetrics", {})
                        return {
                            "power_consumed_watts": pc.get("PowerConsumedWatts"),
                            "average_consumed_watts": metrics.get(
                                "AverageConsumedWatts"
                            ),
                            "max_consumed_watts": metrics.get("MaxConsumedWatts"),
                            "min_consumed_watts": metrics.get("MinConsumedWatts"),
                        }
        except httpx2.HTTPError, OSError:
            pass
        return None

    def get_bios_settings(self, node_id: int) -> dict[str, Any]:
        """Retrieves active BIOS attributes from Redfish."""
        try:
            with self.get_client(node_id) as client:
                resp = client.get("/redfish/v1/Systems/1/Bios/")
                if resp.status_code == 200:
                    return resp.json().get("Attributes", {})
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to fetch BIOS settings for Node %s: %s", node_id, err)
        return {}

    def get_pending_bios_settings(self, node_id: int) -> dict[str, Any]:
        """Retrieves pending (staged) BIOS attributes waiting for server reboot."""
        try:
            with self.get_client(node_id) as client:
                resp = client.get("/redfish/v1/systems/1/bios/settings/")
                if resp.status_code == 200:
                    return resp.json().get("Attributes", {})
        except (httpx2.HTTPError, OSError) as err:
            logger.debug(
                "Failed to fetch pending BIOS settings for Node %s: %s", node_id, err
            )
        return {}

    def set_bios_settings(self, node_id: int, attributes: dict[str, Any]) -> bool:
        """Stages BIOS attribute changes via PATCH to pending settings endpoint."""
        if not attributes:
            return True
        try:
            with self.get_client(node_id) as client:
                resp = client.patch(
                    "/redfish/v1/systems/1/bios/settings/",
                    {"Attributes": attributes},
                )
                return resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to stage BIOS settings for Node %s: %s", node_id, err)
            return False

    def backup_bios(self, node_id: int, output_path: Path) -> Path:
        """Dumps complete active BIOS attributes for a node into a formatted JSON file."""
        attrs = self.get_bios_settings(node_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(attrs, indent=2), encoding="utf-8")
        return output_path
