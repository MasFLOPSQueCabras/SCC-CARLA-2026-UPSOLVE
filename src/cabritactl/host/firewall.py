"""Narrow firewall rules with receipts; never flush or replace a host ruleset."""

import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cabritactl.core.resolved import ResolvedCluster
from cabritactl.host.setup import active_firewall, probe


@dataclass
class Rule:
    add: list[str]
    remove: list[str]
    query: list[str]


def rules_for(cluster: ResolvedCluster, manager: str) -> list[Rule]:
    net = cluster.manifest.network
    if not net.managed:
        raise ValueError("Automatic firewall setup requires a managed NAT network")
    from cabritactl.providers.libvirt_backend.network import validate_network

    validate_network(cluster.manifest)
    rules = []
    if manager == "ufw":
        specs = [
            [
                "allow",
                "in",
                "on",
                net.bridge,
                "from",
                net.subnet,
                "to",
                net.gateway,
                "port",
                "53",
                "proto",
                "udp",
            ],
            [
                "allow",
                "in",
                "on",
                net.bridge,
                "from",
                net.subnet,
                "to",
                net.gateway,
                "port",
                "53",
                "proto",
                "tcp",
            ],
            [
                "allow",
                "in",
                "on",
                net.bridge,
                "to",
                "any",
                "port",
                "67",
                "proto",
                "udp",
            ],
            ["route", "allow", "in", "on", net.bridge, "from", net.subnet],
        ]
        if cluster.manifest.bootstrap.method == "golden-restore":
            host, port = cluster.recovery_endpoint()
            if host != net.gateway:
                raise ValueError(
                    "Managed recovery HTTP must bind to the cluster gateway"
                )
            specs.append(
                [
                    "allow",
                    "in",
                    "on",
                    net.bridge,
                    "from",
                    net.subnet,
                    "to",
                    host,
                    "port",
                    str(port),
                    "proto",
                    "tcp",
                ]
            )
        for spec in specs:
            # UFW's delete syntax places delete after route for routed rules.
            remove = (
                ["ufw", "route", "delete", *spec[1:]]
                if spec[0] == "route"
                else ["ufw", "delete", *spec]
            )
            rules.append(Rule(["ufw", *spec], remove, ["ufw", "show", "added"]))
    elif (
        manager == "firewalld" and cluster.manifest.bootstrap.method == "golden-restore"
    ):
        host, port = cluster.recovery_endpoint()
        if host != net.gateway:
            raise ValueError("Managed recovery HTTP must bind to the cluster gateway")
        rich = f'rule family="ipv4" source address="{net.subnet}" destination address="{host}" port port="{port}" protocol="tcp" accept'
        for permanent in ([], ["--permanent"]):
            prefix = ["firewall-cmd", *permanent, "--zone=libvirt"]
            rules.append(
                Rule(
                    [*prefix, f"--add-rich-rule={rich}"],
                    [*prefix, f"--remove-rich-rule={rich}"],
                    [*prefix, f"--query-rich-rule={rich}"],
                )
            )
    elif manager not in ("firewalld", "none"):
        raise ValueError(
            f"Firewall manager is {manager}; inspect policy manually. No automatic rules will be changed."
        )
    return rules


def privileged(argv: list[str]) -> list[str]:
    return (["sudo"] if os.geteuid() else []) + argv


def present(rule: Rule) -> bool:
    result = probe(privileged(rule.query))
    if rule.add[0] == "firewall-cmd":
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr or result.stdout)
        return result.returncode == 0
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    target = rule.add
    for line in result.stdout.splitlines():
        try:
            args = shlex.split(line)
        except ValueError:
            continue
        if args == target:
            return True
    return False


def receipt_path(cluster: ResolvedCluster) -> Path:
    from cabritactl.paths import get_state_dir

    return cluster.state_directory(get_state_dir()) / "host-firewall.json"


def apply_rules(cluster: ResolvedCluster) -> None:
    manager = active_firewall()
    rules = rules_for(cluster, manager)
    receipt = receipt_path(cluster)
    old: dict[str, Any] = (
        json.loads(receipt.read_text())
        if receipt.exists()
        else {"manager": manager, "rules": []}
    )
    if old["manager"] != manager:
        raise ValueError(
            "Firewall manager changed; clean up recorded rules with the original manager first"
        )
    for rule in rules:
        if present(rule):
            continue
        result = subprocess.run(
            privileged(rule.add),
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        if rule.add[0] == "ufw" and "Skipping adding existing rule" in result.stdout:
            continue
        if asdict(rule) not in old["rules"]:
            old["rules"].append(asdict(rule))
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(old, indent=2))
        temporary.replace(receipt)


def cleanup_rules(cluster: ResolvedCluster, apply: bool = False) -> list[list[str]]:
    receipt = receipt_path(cluster)
    if not receipt.exists():
        return []
    old = json.loads(receipt.read_text())
    allowed = [asdict(r) for r in rules_for(cluster, old["manager"])]
    if any(record not in allowed for record in old["rules"]):
        raise ValueError(
            "Manifest differs from firewall receipt; restore the original manifest before cleanup"
        )
    # No commands from an unchecked receipt may be elevated.
    rules = [Rule(**record) for record in old["rules"]]
    if apply:
        result = probe(["virsh", "-c", "qemu:///system", "net-list", "--all", "--name"])
        if result.returncode:
            raise RuntimeError(
                "Cannot verify that the cluster network has been destroyed"
            )
        if cluster.manifest.network.network_name in result.stdout.splitlines():
            raise ValueError(
                "Destroy the cluster network before removing its host rules"
            )
        for rule in list(rules):
            if present(rule):
                subprocess.run(privileged(rule.remove), check=True)
            old["rules"].remove(asdict(rule))
            receipt.write_text(json.dumps(old, indent=2))
        receipt.unlink()
    return [privileged(rule.remove) for rule in rules]
