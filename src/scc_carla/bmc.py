import logging
import socket
import subprocess
import time
from types import TracebackType
from typing import Any, Self

import httpx2

from scc_carla.config import ClusterSettings
from scc_carla.http_server import is_running_on_bastion

logger = logging.getLogger(__name__)


class SSHSocksTunnel:
    """Manages an ephemeral SSH SOCKS5 tunnel to the bastion host."""

    def __init__(self, bastion_ssh_host: str, local_port: int = 10872) -> None:
        self.bastion_ssh_host = bastion_ssh_host
        self.local_port = local_port
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 10.0) -> None:
        if self._is_port_open():
            return

        self.process = subprocess.Popen(
            ["ssh", "-D", str(self.local_port), "-N", self.bastion_ssh_host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        start = time.time()
        while time.time() - start < timeout:
            if self._is_port_open():
                return
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
        settings: ClusterSettings,
        proxy_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.bmc_ip = bmc_ip
        self.settings = settings
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
        if not self.settings.bmc_user or not self.settings.bmc_password:
            return False

        url = f"{self.base_url}/redfish/v1/SessionService/Sessions/"
        payload = {
            "UserName": self.settings.bmc_user,
            "Password": self.settings.bmc_password,
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
    """Orchestrator for node BMC management via Redfish."""

    def __init__(self, settings: ClusterSettings) -> None:
        self.settings = settings
        self.on_bastion = is_running_on_bastion(settings.bastion_hostname)
        self.socks_port = 10872
        self._tunnel: SSHSocksTunnel | None = None

    def _ensure_transport(self) -> str | None:
        """Returns proxy_url for httpx2 if off-bastion, managing the SOCKS tunnel."""
        if self.on_bastion:
            return None

        if self._tunnel is None:
            self._tunnel = SSHSocksTunnel(
                bastion_ssh_host=self.settings.bastion_ssh_host,
                local_port=self.socks_port,
            )
            self._tunnel.start()
        return f"socks5://127.0.0.1:{self.socks_port}"

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

    def get_client(self, node_id: int) -> RedfishClient:
        proxy_url = self._ensure_transport()
        bmc_ip = self.settings.get_bmc_ip(node_id)
        return RedfishClient(bmc_ip=bmc_ip, settings=self.settings, proxy_url=proxy_url)

    def get_power_status(self, node_id: int) -> str:
        try:
            with self.get_client(node_id) as client:
                resp = client.get("/redfish/v1/Systems/1/")
                if resp.status_code == 200:
                    power = resp.json().get("PowerState", "").upper()
                    if power == "ON":
                        return "ON"
                    if power == "OFF":
                        return "OFF"
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to get power status for Node %s: %s", node_id, err)
        return "UNKNOWN"

    def power_off(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": "ForceOff"},
                )
                return resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to power off Node %s: %s", node_id, err)
            return False

    def power_on(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": "On"},
                )
                return resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to power on Node %s: %s", node_id, err)
            return False

    def reset(self, node_id: int, reset_type: str = "ForceRestart") -> bool:
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                return resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to reset Node %s: %s", node_id, err)
            return False

    def eject_virtual_media(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                for slot in (1, 2):
                    client.post(
                        f"/redfish/v1/Managers/1/VirtualMedia/{slot}/Actions/VirtualMedia.EjectMedia/",
                        {},
                    )
                return True
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to eject media for Node %s: %s", node_id, err)
            return False

    def mount_and_boot(
        self, node_id: int, iso_url: str, cidata_url: str | None = None
    ) -> bool:
        try:
            with self.get_client(node_id) as client:
                # 1. Eject any existing virtual media
                for slot in (1, 2):
                    client.post(
                        f"/redfish/v1/Managers/1/VirtualMedia/{slot}/Actions/VirtualMedia.EjectMedia/",
                        {},
                    )

                # 2. Insert cidata into Slot 1 (Floppy/USBStick) if provided
                if cidata_url:
                    resp1 = client.post(
                        "/redfish/v1/Managers/1/VirtualMedia/1/Actions/VirtualMedia.InsertMedia/",
                        {"Image": cidata_url},
                    )
                    if resp1.status_code not in (200, 204):
                        return False

                # 3. Insert OS ISO into Slot 2 (CD/DVD)
                resp2 = client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia/",
                    {"Image": iso_url},
                )
                if resp2.status_code not in (200, 204):
                    return False

                # 4. Configure Slot 2 to BootOnNextServerReset via OEM PATCH
                patch_resp = client.patch(
                    "/redfish/v1/Managers/1/VirtualMedia/2/",
                    {"Oem": {"Hpe": {"BootOnNextServerReset": True}}},
                )
                if patch_resp.status_code not in (200, 204):
                    return False

                # 5. Check current power state and trigger power on or reboot
                sys_resp = client.get("/redfish/v1/Systems/1/")
                power = (
                    sys_resp.json().get("PowerState", "").upper()
                    if sys_resp.status_code == 200
                    else "UNKNOWN"
                )
                reset_type = "On" if power == "OFF" else "ForceRestart"
                boot_resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                return boot_resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to mount and boot Node %s: %s", node_id, err)
            return False
