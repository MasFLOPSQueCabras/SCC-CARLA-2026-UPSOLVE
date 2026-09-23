import json
import subprocess
import xml.etree.ElementTree as ET
from ipaddress import IPv4Network
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from cabritactl.cli import app
from cabritactl.core.manifest import parse_manifest
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.host.doctor import Check, report
from cabritactl.host.firewall import cleanup_rules, rules_for
from cabritactl.host.setup import package_steps
from cabritactl.providers.libvirt_backend.network import (
    check_network,
    ensure_network,
    remove_network,
    select_subnet,
    validate_network,
)
from cabritactl.providers.libvirt_backend.storage import ManagedStorage, volume_xml


def cluster(tmp_path):
    m = parse_manifest("""name: host-test
network:
  managed: true
  network_name: cabrita-host-test
  bridge: cbrtest
  subnet: 192.168.50.0/24
  gateway: 192.168.50.1
  dns: 192.168.50.1
nodes:
- {id: 1, hostname: node1, ip: 192.168.50.101, mac: '52:54:00:00:00:01'}
""")
    return ResolvedCluster(tmp_path / "cluster.yaml", m)


def test_subnet_allocation_excludes_routes_and_exhaustion():
    assert select_subnet([IPv4Network("192.168.0.0/23")]) == IPv4Network(
        "192.168.2.0/24"
    )
    with pytest.raises(ValueError, match="No free"):
        select_subnet([IPv4Network("192.168.0.0/16")])


@pytest.mark.parametrize(
    "address", ["192.168.50.1", "192.168.50.255", "192.168.50.44", "10.0.0.2"]
)
def test_invalid_static_addresses(tmp_path, address):
    m = cluster(tmp_path).manifest
    m.nodes[0].ip = address
    with pytest.raises(ValueError):
        validate_network(m)


def test_duplicate_macs_are_case_insensitive():
    with pytest.raises(ValueError, match="Duplicate MAC"):
        parse_manifest("""name: duplicate
nodes:
- {id: 1, hostname: a, ip: 192.0.2.1, mac: '52:54:00:AA:00:01'}
- {id: 2, hostname: b, ip: 192.0.2.2, mac: '52:54:00:aa:00:01'}
""")


def test_managed_network_recheck_prevents_creation_on_conflict(tmp_path, monkeypatch):
    conn = Mock()
    conn.listAllNetworks.return_value = []
    monkeypatch.setattr(
        "cabritactl.providers.libvirt_backend.network.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, json.dumps([{"dst": "192.168.50.0/24", "dev": "vpn0"}]), ""
        ),
    )
    with pytest.raises(ValueError, match="overlaps"):
        ensure_network(conn, cluster(tmp_path).manifest)
    conn.networkDefineXML.assert_not_called()


def test_unowned_network_never_adopted(tmp_path):
    net = Mock()
    net.name.return_value = "cabrita-host-test"
    net.XMLDesc.return_value = "<network><name>cabrita-host-test</name></network>"
    conn = Mock()
    conn.listAllNetworks.return_value = [net]
    with pytest.raises(ValueError, match="not owned"):
        check_network(conn, cluster(tmp_path).manifest)
    with pytest.raises(ValueError, match="unowned"):
        remove_network(conn, cluster(tmp_path).manifest)
    net.destroy.assert_not_called()


def test_firewall_rules_are_scoped_and_have_inverse(tmp_path):
    c = cluster(tmp_path)
    rules = rules_for(c, "ufw")
    assert len(rules) == 4
    assert all("cbrtest" in r.add for r in rules)
    assert all("delete" in r.remove for r in rules)
    assert all("reset" not in r.add for r in rules)
    assert rules_for(c, "firewalld") == []
    with pytest.raises(ValueError, match="custom"):
        rules_for(c, "custom")


def test_recovery_http_cannot_bind_external_interface(tmp_path):
    c = cluster(tmp_path)
    c.manifest.bootstrap.method = "golden-restore"
    c.manifest.bootstrap.inputs = {"http_bind_ip": "0.0.0.0"}
    with pytest.raises(ValueError, match="gateway"):
        rules_for(c, "firewalld")


def test_cleanup_rejects_tampered_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "manager": "ufw",
                "rules": [
                    {"add": ["anything"], "remove": ["rm", "-rf", "/"], "query": []}
                ],
            }
        )
    )
    monkeypatch.setattr("cabritactl.host.firewall.receipt_path", lambda c: receipt)
    with pytest.raises(ValueError, match="differs"):
        cleanup_rules(cluster(tmp_path), True)


def test_unknown_checks_are_not_passes():
    result = report(
        [Check("policy", "unknown", "Cannot read"), Check("kvm", "fail", "Missing")]
    )
    assert not result["ok"]
    assert result["checks"][0]["status"] == "unknown"
    assert result["problems"] == ["kvm: Missing"]


def test_base_setup_preview_does_not_apply(monkeypatch):
    monkeypatch.setattr("cabritactl.commands.host.distro", lambda: "fedora")
    monkeypatch.setattr("cabritactl.commands.host.daemon_steps", lambda _: [])
    apply = Mock()
    monkeypatch.setattr("cabritactl.commands.host.run_steps", apply)
    result = CliRunner().invoke(app, ["host", "setup", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["argv"][:2] == ["dnf", "install"]
    apply.assert_not_called()


def test_distro_packages_and_recovery_options():
    assert "libvirt-devel" in package_steps("fedora")[0].argv
    assert "libvirt-dev" in package_steps("ubuntu")[-1].argv
    assert "guestfs-tools" in package_steps("fedora", True)[0].argv
    assert "libguestfs-tools" in package_steps("ubuntu", True)[-1].argv


def test_volume_xml_uses_restricted_permissions_and_explicit_backing():
    root = ET.fromstring(
        volume_xml(
            "disk.qcow2", 1024, "qcow2", backing="/var/lib/libvirt/images/base.qcow2"
        )
    )
    assert root.findtext("target/permissions/mode") == "0600"
    fmt = root.find("backingStore/format")
    assert fmt is not None and fmt.get("type") == "qcow2"
    assert root.find("description") is None  # Not supported by libvirt's volume schema.


class Volume:
    def __init__(self, pool, name):
        self.pool, self._name = pool, name
        self.content = b""

    def name(self):
        return self._name

    def path(self):
        return "/var/lib/libvirt/images/cabrita/" + self._name

    def key(self):
        return self.path()

    def upload(self, stream, *args):
        stream.volume = self

    def download(self, stream, *args):
        stream.volume = self

    def delete(self, flags):
        del self.pool.volumes[self._name]


class Stream:
    volume: Volume

    def sendAll(self, callback, opaque):
        data = callback(self, 1024, None)
        while data:
            self.volume.content += data
            data = callback(self, 1024, None)

    def recvAll(self, callback, opaque):
        callback(self, self.volume.content, None)

    def finish(self):
        pass

    def abort(self):
        pass


class Pool:
    def __init__(self):
        self.volumes = {}

    def name(self):
        return "cabrita"

    def isActive(self):
        return True

    def XMLDesc(self, flags):
        return '<pool type="dir"><target><path>/var/lib/libvirt/images/cabrita</path></target></pool>'

    def listAllVolumes(self):
        return list(self.volumes.values())

    def createXML(self, xml, flags):
        name = ET.fromstring(xml).findtext("name")
        volume = Volume(self, name)
        self.volumes[name] = volume
        return volume


def storage(tmp_path, monkeypatch):
    monkeypatch.setattr("cabritactl.paths.get_state_dir", lambda: tmp_path)
    pool = Pool()
    conn = Mock()
    conn.listAllStoragePools.return_value = [pool]
    conn.newStream.side_effect = lambda _: Stream()
    conn.listAllDomains.return_value = []
    return ManagedStorage(conn, "cabrita", "host-test"), pool


def test_artifact_upload_checksum_and_shared_reuse(tmp_path, monkeypatch):
    store, pool = storage(tmp_path, monkeypatch)
    source = tmp_path / "base.qcow2"
    source.write_bytes(b"image content" * 1000)
    path = store.import_file(source)
    assert store.import_file(source) == path
    assert len(pool.volumes) == 1
    assert next(iter(pool.volumes.values())).content == source.read_bytes()
    assert store.records()[path.name]["key"] == str(path)


def test_partial_owned_upload_is_repaired_but_unowned_is_not(tmp_path, monkeypatch):
    store, pool = storage(tmp_path, monkeypatch)
    source = tmp_path / "base.qcow2"
    source.write_bytes(b"complete")
    path = store.import_file(source)
    pool.volumes[path.name].content = b"partial"
    assert store.import_file(source) == path
    store.receipt.unlink()
    pool.volumes[path.name].content = b"foreign"
    with pytest.raises(ValueError, match="Unowned"):
        store.import_file(source)
    assert pool.volumes[path.name].content == b"foreign"


def test_destroy_keeps_shared_base_and_refuses_unowned(tmp_path, monkeypatch):
    store, pool = storage(tmp_path, monkeypatch)
    source = tmp_path / "base.qcow2"
    source.write_bytes(b"base")
    base = store.import_file(source)
    disk = store.disk("cabrita-host-test-node1.qcow2", 1, base)
    store.remove(disk.name)
    assert base.name in pool.volumes
    with pytest.raises(ValueError, match="unowned"):
        store.remove(base.name)


def test_failed_upload_removes_partial_volume(tmp_path, monkeypatch):
    store, pool = storage(tmp_path, monkeypatch)
    source = tmp_path / "base.qcow2"
    source.write_bytes(b"base")
    monkeypatch.setattr(
        Stream, "sendAll", lambda *args: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError, match="interrupted"):
        store.import_file(source)
    assert not pool.volumes


def test_firewall_apply_is_repeatable_and_does_not_adopt_existing(
    tmp_path, monkeypatch
):
    from cabritactl.host import firewall

    c = cluster(tmp_path)
    rules = rules_for(c, "ufw")
    existing = {tuple(rules[0].add)}
    receipt = tmp_path / "rules.json"
    monkeypatch.setattr(firewall, "receipt_path", lambda _: receipt)
    monkeypatch.setattr(firewall, "active_firewall", lambda: "ufw")
    monkeypatch.setattr(firewall, "present", lambda r: tuple(r.add) in existing)
    calls = []

    def run(argv, **kwargs):
        actual = argv[1:] if argv[0] == "sudo" else argv
        calls.append(actual)
        existing.add(tuple(actual))
        return subprocess.CompletedProcess(argv, 0, "Rule added", "")

    monkeypatch.setattr(firewall.subprocess, "run", run)
    firewall.apply_rules(c)
    firewall.apply_rules(c)
    assert len(calls) == len(rules) - 1
    saved = json.loads(receipt.read_text())["rules"]
    assert len(saved) == len(rules) - 1
    assert all(record["add"] != rules[0].add for record in saved)


def test_firewall_cleanup_requires_destroyed_network(tmp_path, monkeypatch):
    from dataclasses import asdict

    from cabritactl.host import firewall

    c = cluster(tmp_path)
    receipt = tmp_path / "rules.json"
    receipt.write_text(
        json.dumps({"manager": "ufw", "rules": [asdict(rules_for(c, "ufw")[0])]})
    )
    monkeypatch.setattr(firewall, "receipt_path", lambda _: receipt)
    monkeypatch.setattr(
        firewall,
        "probe",
        lambda _: subprocess.CompletedProcess([], 0, "cabrita-host-test\n", ""),
    )
    with pytest.raises(ValueError, match="Destroy"):
        firewall.cleanup_rules(c, True)
    assert receipt.exists()


def test_network_cleanup_keeps_network_used_by_foreign_domain(tmp_path, monkeypatch):
    import os

    c = cluster(tmp_path)
    net = Mock()
    net.name.return_value = c.manifest.network.network_name
    net.XMLDesc.return_value = f'<network><metadata><owner xmlns="https://cabritactl.org/network/1" cluster="host-test" uid="{os.getuid()}"/></metadata></network>'
    domain = Mock()
    domain.XMLDesc.return_value = '<domain><devices><interface><source network="cabrita-host-test"/></interface></devices></domain>'
    conn = Mock()
    conn.listAllNetworks.return_value = [net]
    conn.listAllDomains.return_value = [domain]
    remove_network(conn, c.manifest)
    net.destroy.assert_not_called()
    net.undefine.assert_not_called()


def test_active_firewall_refuses_competing_managers(monkeypatch):
    from cabritactl.host import setup

    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/tool")

    def query(argv):
        output = "running\n" if argv[0] == "firewall-cmd" else "Status: active\n"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(setup, "probe", query)
    with pytest.raises(ValueError, match="Both"):
        setup.active_firewall()


@pytest.mark.parametrize(
    "policy", ["empty", "drop-rule", "drop-policy", "missing-target"]
)
def test_inactive_ufw_scaffolding(monkeypatch, policy):
    from cabritactl.host import setup

    common = {"family": "ip", "table": "filter"}
    entries = [
        {"chain": common | {"name": "INPUT", "policy": "accept"}},
        {"chain": common | {"name": "ufw-before-input"}},
        {
            "rule": common
            | {
                "chain": "INPUT",
                "expr": [
                    {"counter": {"packets": 0, "bytes": 0}},
                    {"jump": {"target": "ufw-before-input"}},
                ],
            }
        },
    ]
    if policy == "drop-rule":
        entries.append(
            {"rule": common | {"chain": "ufw-before-input", "expr": [{"drop": None}]}}
        )
    elif policy == "drop-policy":
        entries[0]["chain"]["policy"] = "drop"
    elif policy == "missing-target":
        entries.pop(1)

    def query(argv):
        output = {
            "ufw": "Status: inactive\n",
            "nft": json.dumps({"nftables": entries}),
        }.get(argv[0], "")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(setup, "probe", query)
    assert setup.active_firewall() == ("none" if policy == "empty" else "custom")


def test_firmware_ignores_confidential_rom_and_selects_split_flash(tmp_path):
    from cabritactl.providers.libvirt_backend.firmware import select_firmware

    code, nvram = tmp_path / "CODE.fd", tmp_path / "VARS.fd"
    code.touch()
    nvram.touch()
    common = {
        "interface-types": ["uefi"],
        "targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}],
        "features": ["amd-sev"],
    }
    (tmp_path / "10-amdsev.json").write_text(
        json.dumps(
            common
            | {
                "mapping": {
                    "device": "memory",
                    "filename": "/usr/share/ovmf/OVMF.amdsev.fd",
                }
            }
        )
    )
    (tmp_path / "60-standard.json").write_text(
        json.dumps(
            common
            | {
                "mapping": {
                    "device": "flash",
                    "executable": {"filename": str(code), "format": "raw"},
                    "nvram-template": {"filename": str(nvram), "format": "raw"},
                }
            }
        )
    )
    result = select_firmware(
        "<domainCapabilities><machine>pc-q35-10.2</machine><arch>x86_64</arch></domainCapabilities>",
        (tmp_path,),
    )
    assert result["firmware_loader"] == str(code)
    assert result["firmware_nvram"] == str(nvram)


def test_isolated_tool_finds_dependency_executables(tmp_path, monkeypatch):
    from cabritactl.core.executables import find_executable

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ansible-playbook"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(bin_dir / "python"))
    monkeypatch.setenv("PATH", "/no-tools")
    assert find_executable("ansible-playbook") == str(tool)
