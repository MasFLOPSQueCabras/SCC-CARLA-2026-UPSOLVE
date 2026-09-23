"""Run only inside a disposable Ubuntu container with CAP_NET_ADMIN."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from cabritactl.core.bootstrap import BootstrapMethod
from cabritactl.core.manifest import parse_manifest
from cabritactl.core.resolved import ResolvedCluster
from cabritactl.host import firewall


def run(*argv):
    return subprocess.check_output(argv, text=True)


run("ip", "link", "add", "cbrtest", "type", "bridge")
run("ip", "address", "add", "192.168.50.1/24", "dev", "cbrtest")
run("ip", "link", "set", "cbrtest", "up")
run("ufw", "allow", "12345/tcp")
run("ufw", "--force", "enable")
original = run("ufw", "show", "added")
manifest = parse_manifest("""name: ufw-acceptance
network:
  managed: true
  network_name: cabrita-ufw-acceptance
  bridge: cbrtest
  subnet: 192.168.50.0/24
  gateway: 192.168.50.1
  dns: 192.168.50.1
nodes:
- {id: 1, hostname: node1, ip: 192.168.50.101, mac: '52:54:00:00:00:01'}
""")
cluster = ResolvedCluster(Path("/tmp/cluster.yaml"), manifest)
firewall.apply_rules(cluster)
receipt = json.loads(firewall.receipt_path(cluster).read_text())
assert len(receipt["rules"]) == 4, receipt
firewall.apply_rules(cluster)
assert json.loads(firewall.receipt_path(cluster).read_text()) == receipt
assert all(firewall.present(rule) for rule in firewall.rules_for(cluster, "ufw"))
# Recovery HTTP adds only the chosen cluster subnet/gateway/port rule.
manifest.bootstrap.method = BootstrapMethod.GOLDEN_RESTORE
firewall.apply_rules(cluster)
assert len(json.loads(firewall.receipt_path(cluster).read_text())["rules"]) == 5
assert all(firewall.present(rule) for rule in firewall.rules_for(cluster, "ufw"))
probe = firewall.probe


# This container has no libvirt daemon; model an already-destroyed test network.
def absent_network(argv: list[str]) -> subprocess.CompletedProcess[str]:
    if argv[:1] == ["virsh"]:
        return subprocess.CompletedProcess(argv, 0, "", "")
    return probe(argv)


with patch.object(firewall, "probe", side_effect=absent_network):
    firewall.cleanup_rules(cluster, True)
assert run("ufw", "show", "added") == original
assert not firewall.receipt_path(cluster).exists()
run("ufw", "disable")
manager = firewall.active_firewall()
assert manager == "none", (manager, run("nft", "-j", "list", "ruleset"))
print(
    "PASS: active UFW rules, repeatability, exact cleanup, unrelated-rule preservation"
)
