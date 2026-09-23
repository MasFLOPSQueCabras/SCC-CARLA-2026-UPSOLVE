from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from cabritactl.core.manifest import parse_manifest


def controller(monkeypatch):
    pytest.importorskip("httpx2", reason="requires the optional Helvetios Python extra")
    from cabritactl.providers.helvetios.bmc_client import BMCController

    bmc = BMCController(parse_manifest("name: redfish\nprovider: helvetios"))
    client = Mock()
    monkeypatch.setattr(bmc, "get_client", lambda *args, **kwargs: nullcontext(client))
    return bmc, client


def test_failed_status_does_not_suppress_other_nodes(monkeypatch):
    bmc, client = controller(monkeypatch)
    response = Mock(status_code=200)
    response.json.return_value = {"PowerState": "On"}
    client.get.side_effect = [OSError("node unavailable"), response]
    with pytest.raises(ConnectionError, match="node unavailable"):
        bmc.get_power_status(1)
    assert bmc.get_power_status(2) == "ON"
    assert client.get.call_count == 2


def test_bios_read_failure_is_not_empty_success(monkeypatch):
    bmc, client = controller(monkeypatch)
    client.get.side_effect = OSError("BMC unavailable")
    with pytest.raises(RuntimeError, match="BMC unavailable"):
        bmc.get_bios_settings(1)


def test_authentication_failure_is_explicit_and_closes_client(monkeypatch):
    pytest.importorskip("httpx2")
    from cabritactl.providers.helvetios.bmc_client import RedfishClient

    client = RedfishClient("192.0.2.1", "user", "secret")
    monkeypatch.setattr(client, "login", lambda: False)
    close = Mock()
    monkeypatch.setattr(client, "close", close)
    with (
        pytest.raises(RuntimeError, match="Redfish authentication failed") as error,
        client,
    ):
        pytest.fail("Unauthenticated requests must not run")
    assert "secret" not in str(error.value)
    close.assert_called_once()


def test_virtual_media_boot_uses_the_hpe_media_privilege(monkeypatch):
    bmc, client = controller(monkeypatch)
    monkeypatch.setattr(bmc, "power_off", lambda *args, **kwargs: True)
    monkeypatch.setattr(bmc, "eject_virtual_media", lambda *args: True)
    monkeypatch.setattr(bmc, "power_on", lambda *args: True)
    responses = []
    for power in ("On", "Off", "Off", "On"):
        response = Mock()
        response.json.return_value = {"PowerState": power}
        responses.append(response)
    client.get.side_effect = responses
    monkeypatch.setattr(
        "cabritactl.providers.helvetios.bmc_client.time.sleep", lambda _: None
    )
    assert bmc.mount_and_boot(1, "http://192.0.2.1/installer.iso")
    assert client.get.call_count == 4
    client.patch.assert_called_once_with(
        "/redfish/v1/Managers/1/VirtualMedia/2/",
        {"Oem": {"Hpe": {"BootOnNextServerReset": True}}},
    )


def test_login_retries_service_unavailable_without_exposing_credentials(monkeypatch):
    pytest.importorskip("httpx2")
    from cabritactl.providers.helvetios.bmc_client import RedfishClient

    client = RedfishClient("192.0.2.1", "user", "secret")
    busy = Mock(status_code=503)
    success = Mock(
        status_code=201, headers={"X-Auth-Token": "token", "Location": "/session/1"}
    )
    post = Mock(side_effect=[busy, success])
    monkeypatch.setattr(client._client, "post", post)
    monkeypatch.setattr(
        "cabritactl.providers.helvetios.bmc_client.time.sleep", lambda _: None
    )
    try:
        assert client.login()
        assert post.call_count == 2
        assert client.auth_token == "token"
    finally:
        client.auth_token = None
        client._client.close()


def test_login_does_not_retry_rejected_credentials(monkeypatch):
    pytest.importorskip("httpx2")
    from cabritactl.providers.helvetios.bmc_client import RedfishClient

    client = RedfishClient("192.0.2.1", "user", "secret")
    post = Mock(return_value=Mock(status_code=401))
    monkeypatch.setattr(client._client, "post", post)
    try:
        with pytest.raises(RuntimeError, match="HTTP 401") as error, client:
            pytest.fail("Rejected credentials must not open a session")
        assert "secret" not in str(error.value)
        post.assert_called_once()
    finally:
        client._client.close()


def test_install_monitor_survives_transient_bmc_failure(monkeypatch):
    from cabritactl.core.bootstrap import BootstrapMethod
    from cabritactl.core.providers.base import PowerState
    from cabritactl.lifecycle import ProviderBackend

    backend = object.__new__(ProviderBackend)
    backend.timeout = 10
    backend.state = Mock()
    backend.state.read.return_value.phase = "installing"
    backend.provider = Mock()
    backend.provider.get_power_status.side_effect = [
        ConnectionError("busy"),
        PowerState.OFF,
    ]
    backend.cluster = Mock()
    backend.cluster.manifest.bootstrap.method = BootstrapMethod.EMBEDDED_KICKSTART
    monkeypatch.setattr(backend, "_reachable", Mock(side_effect=[False, False, True]))
    monkeypatch.setattr("cabritactl.lifecycle.time.sleep", lambda _: None)
    backend.verify(Mock(id=1, hostname="node1"))
    backend.provider.post_provision.assert_called_once_with(1)
    assert backend.provider.get_power_status.call_count == 2
