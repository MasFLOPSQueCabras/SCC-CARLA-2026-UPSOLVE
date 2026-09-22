from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from cabrita.core.manifest import parse_manifest


def controller(monkeypatch):
    pytest.importorskip("httpx2", reason="requires the optional Helvetios Python extra")
    from cabrita.providers.helvetios.bmc_client import BMCController

    bmc = BMCController(parse_manifest("name: redfish\nprovider: helvetios"))
    client = Mock()
    monkeypatch.setattr(bmc, "get_client", lambda *args, **kwargs: nullcontext(client))
    return bmc, client


def test_failed_status_does_not_suppress_other_nodes(monkeypatch):
    bmc, client = controller(monkeypatch)
    response = Mock(status_code=200)
    response.json.return_value = {"PowerState": "On"}
    client.get.side_effect = [OSError("node unavailable"), response]
    with pytest.raises(RuntimeError, match="node unavailable"):
        bmc.get_power_status(1)
    assert bmc.get_power_status(2) == "ON"
    assert client.get.call_count == 2


def test_bios_read_failure_is_not_empty_success(monkeypatch):
    bmc, client = controller(monkeypatch)
    client.get.side_effect = OSError("BMC unavailable")
    with pytest.raises(RuntimeError, match="BMC unavailable"):
        bmc.get_bios_settings(1)
