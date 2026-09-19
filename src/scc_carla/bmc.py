import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2

from scc_carla.config import ClusterSettings
from scc_carla.http_server import is_running_on_bastion

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

    def start(self, timeout: float = 10.0) -> None:
        if self._is_port_open():
            # If the default port is already occupied by another process, allocate a free ephemeral port
            self.local_port = _find_free_local_port()

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
                    logger.debug(
                        "GracefulShutdown returned %s on Node %s, falling back to ForceOff",
                        resp.status_code,
                        node_id,
                    )
                    resp = client.post(
                        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                        {"ResetType": "ForceOff"},
                    )
                    return resp.status_code in (200, 204) or "Power is off" in resp.text
                return False
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
                return resp.status_code in (200, 204) or "Power is on" in resp.text
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to power on Node %s: %s", node_id, err)
            return False

    def reset(self, node_id: int, graceful: bool = False) -> bool:
        reset_type = "GracefulRestart" if graceful else "ForceRestart"
        try:
            with self.get_client(node_id) as client:
                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                if resp.status_code in (200, 204):
                    return True
                if graceful:
                    resp = client.post(
                        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                        {"ResetType": "ForceRestart"},
                    )
                    return resp.status_code in (200, 204)
                return False
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to reset Node %s: %s", node_id, err)
            return False

    def get_power_metrics(self, node_id: int) -> dict[str, Any]:
        """Fetches live power consumption telemetry from BMC Redfish."""
        metrics: dict[str, Any] = {
            "node_id": node_id,
            "power_state": "UNKNOWN",
            "current_watts": None,
            "average_watts": None,
            "max_watts": None,
            "min_watts": None,
            "interval_min": None,
        }
        try:
            with self.get_client(node_id) as client:
                sys_resp = client.get("/redfish/v1/Systems/1/")
                if sys_resp.status_code == 200:
                    metrics["power_state"] = (
                        sys_resp.json().get("PowerState", "UNKNOWN").upper()
                    )

                resp = client.get("/redfish/v1/Chassis/1/Power/")
                if resp.status_code == 200:
                    data = resp.json()
                    pwr_controls = data.get("PowerControl", [])
                    if pwr_controls:
                        pwr_ctrl = pwr_controls[0]
                        metrics["current_watts"] = pwr_ctrl.get(
                            "PowerConsumedWatts"
                        )
                        pwr_metrics = pwr_ctrl.get("PowerMetrics", {})
                        metrics["average_watts"] = pwr_metrics.get(
                            "AverageConsumedWatts"
                        )
                        metrics["max_watts"] = pwr_metrics.get(
                            "MaxConsumedWatts"
                        )
                        metrics["min_watts"] = pwr_metrics.get(
                            "MinConsumedWatts"
                        )
                        metrics["interval_min"] = pwr_metrics.get(
                            "IntervalInMin"
                        )
        except (httpx2.HTTPError, OSError) as err:
            logger.debug(
                "Failed to get power metrics for Node %s: %s", node_id, err
            )
        return metrics

    def eject_virtual_media(self, node_id: int) -> bool:
        try:
            with self.get_client(node_id) as client:
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/1/Actions/VirtualMedia.EjectMedia/",
                    {},
                )
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia/",
                    {},
                )
                return True
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to eject virtual media on Node %s: %s", node_id, err)
            return False

    def mount_and_boot(
        self,
        node_id: int,
        iso_url: str,
        floppy_url: str | None = None,
    ) -> bool:
        try:
            with self.get_client(node_id) as client:
                # 1. Eject any existing virtual media in Slot 1 and Slot 2
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/1/Actions/VirtualMedia.EjectMedia/",
                    {},
                )
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia/",
                    {},
                )

                # 2. Insert Floppy / OEMDRV in Slot 1 if provided
                if floppy_url:
                    client.post(
                        "/redfish/v1/Managers/1/VirtualMedia/1/Actions/VirtualMedia.InsertMedia/",
                        {"Image": floppy_url, "Inserted": True},
                    )

                # 3. Insert OS ISO in Slot 2 (CD/DVD)
                client.post(
                    "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia/",
                    {"Image": iso_url, "Inserted": True},
                )

                # 4. Set One-Time Boot flag on Slot 2 via HPE OEM property
                client.patch(
                    "/redfish/v1/Managers/1/VirtualMedia/2/",
                    {"Oem": {"Hpe": {"BootOnNextServerReset": True}}},
                )

                # 5. Boot server from virtual media (turn On if powered off, ForceRestart if powered on)
                sys_resp = client.get("/redfish/v1/Systems/1/")
                power = (
                    sys_resp.json().get("PowerState", "").upper()
                    if sys_resp.status_code == 200
                    else ""
                )
                reset_type = "On" if power == "OFF" else "ForceRestart"

                resp = client.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset/",
                    {"ResetType": reset_type},
                )
                return resp.status_code in (200, 204)
        except (httpx2.HTTPError, OSError) as err:
            logger.debug("Failed to mount and boot Node %s: %s", node_id, err)
            return False

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
                payload = {"Attributes": attributes}
                resp = client.patch("/redfish/v1/systems/1/bios/settings/", payload)
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
