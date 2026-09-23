#!/usr/bin/env python3
"""Read assigned BMC inventory; never save authentication tokens or credentials."""

import json
from pathlib import Path

from cabritactl.config import ClusterSettings
from cabritactl.providers.helvetios.bmc_client import RedfishClient, SSHSocksTunnel


def inventory() -> None:
    settings = ClusterSettings()
    if not settings.bmc_user or not settings.bmc_password:
        raise SystemExit("Set CABRITA_BMC_USER and CABRITA_BMC_PASSWORD in .env")
    output = Path("test-results/helvetios-submission/inventory")
    output.mkdir(parents=True, exist_ok=True)
    with SSHSocksTunnel("scc-bastion") as tunnel:
        for node in (1, 2, 3):
            with RedfishClient(
                f"10.1.72.{node}",
                settings.bmc_user,
                settings.bmc_password,
                f"socks5://127.0.0.1:{tunnel.local_port}",
                timeout=30,
            ) as client:
                data = {}
                pending = [
                    "/redfish/v1/Systems/1/",
                    "/redfish/v1/Systems/1/EthernetInterfaces/",
                    "/redfish/v1/Systems/1/Storage/",
                    "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/",
                    "/redfish/v1/Systems/1/Bios/",
                    "/redfish/v1/Systems/1/Bios/Boot/",
                    "/redfish/v1/Managers/1/VirtualMedia/",
                ]
                while pending:
                    path = pending.pop(0)
                    if path in data:
                        continue
                    response = client.get(path)
                    if response.status_code == 404 or (
                        response.status_code == 400 and path.endswith("/Storage/")
                    ):
                        data[path] = {"status": response.status_code}
                        continue
                    response.raise_for_status()
                    value = response.json()
                    data[path] = value
                    for member in value.get("Members", []) + value.get("Drives", []):
                        pending.append(member["@odata.id"])
                    for key in ("LogicalDrives", "DiskDrives"):
                        if key in value:
                            pending.append(value[key]["@odata.id"])
                (output / f"node{node}.json").write_text(json.dumps(data, indent=2))
                system = data["/redfish/v1/Systems/1/"]
                print(
                    json.dumps(
                        {
                            "node": node,
                            "model": system.get("Model"),
                            "power": system.get("PowerState"),
                            "cpu": system.get("ProcessorSummary"),
                            "memory": system.get("MemorySummary"),
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    inventory()
