#!/usr/bin/env python3
"""Ansible Dynamic Inventory for SCC CARLA.

Reads cluster topology, host IPs, roles, and network parameters dynamically from:
1. CABRITA_CLUSTER_MANIFEST / CABRITA_MANIFEST environment variable
2. CLI argument --manifest <path>
3. values.yaml in current working directory or repository root
4. Named cluster config in configs/clusters/${CABRITA_CLUSTER}.yaml
5. Default cluster config in configs/clusters/helvetios-hpc.yaml (or vm-standard.yaml)
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _find_repo_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "pyproject.toml").exists() or (p / ".git").exists():
            return p
        p = p.parent
    return Path.cwd()


def _locate_manifest_path(explicit_manifest: str | None = None) -> Path | None:
    if explicit_manifest:
        p = Path(explicit_manifest)
        if p.exists():
            return p.resolve()

    env_manifest = os.environ.get("CABRITA_CLUSTER_MANIFEST") or os.environ.get(
        "CABRITA_MANIFEST"
    )
    if env_manifest:
        p = Path(env_manifest)
        if p.exists():
            return p.resolve()

    # Check values.yaml in CWD
    cwd_values = Path.cwd() / "values.yaml"
    if cwd_values.exists():
        return cwd_values.resolve()

    repo_root = _find_repo_root()
    repo_values = repo_root / "values.yaml"
    if repo_values.exists():
        return repo_values.resolve()

    cluster_name = os.environ.get("CABRITA_CLUSTER")
    if cluster_name:
        named_cfg = repo_root / "configs" / "clusters" / f"{cluster_name}.yaml"
        if named_cfg.exists():
            return named_cfg.resolve()

    # Default fallbacks
    for fallback in [
        repo_root / "configs" / "clusters" / "helvetios-hpc.yaml",
        repo_root / "configs" / "clusters" / "vm-hw-optimized.yaml",
        repo_root / "configs" / "clusters" / "vm-standard.yaml",
    ]:
        if fallback.exists():
            return fallback.resolve()

    return None


def load_cluster_manifest(manifest_path: Path) -> dict[str, Any]:
    repo_root = _find_repo_root()
    core_src = repo_root / "packages" / "cabrita-core" / "src"
    if core_src.exists() and str(core_src) not in sys.path:
        sys.path.insert(0, str(core_src))

    try:
        from cabrita.core.manifest import load_manifest

        manifest_obj = load_manifest(manifest_path)
        return manifest_obj.model_dump()
    except ImportError, ValueError, OSError, KeyError:
        import yaml

        with open(manifest_path, "r", encoding="utf-8") as f:
            content = f.read()
        for k, v in os.environ.items():
            content = content.replace(f"${{{k}}}", v)
        return yaml.safe_load(content) or {}


def build_inventory(manifest_data: dict[str, Any]) -> dict[str, Any]:
    nodes = manifest_data.get("nodes", [])
    network = manifest_data.get("network", {})
    defaults = manifest_data.get("defaults", {})
    os_spec = defaults.get("os", {})
    provider = manifest_data.get("provider", "libvirt")

    team_id_raw = os.environ.get("CABRITA_TEAM_ID", "72")
    try:
        team_id = int(team_id_raw)
    except ValueError:
        team_id = 72

    username = os_spec.get("username", "scct-2672")
    gateway_ip = network.get("gateway", f"10.2.{team_id}.254")
    dns_ip = network.get("dns", gateway_ip)
    mgmt_network = network.get("subnet", f"10.2.{team_id}.0/24")
    domain = network.get("domain", "cabrita.local")

    hostvars: dict[str, dict[str, Any]] = {}
    cluster_hosts: list[str] = []
    headnodes: list[str] = []
    computenodes: list[str] = []

    headnode_host = None

    for node in nodes:
        hostname = node.get("hostname", f"node{node.get('id', 1)}")
        node_id = node.get("id", 1)
        role = node.get("role", "computenode")
        ip = node.get("ip", "")
        mac = node.get("mac", "")
        bmc = node.get("bmc") or {}

        if role == "headnode" and headnode_host is None:
            headnode_host = hostname

        # InfiniBand IP derivation: 10.10.<team_id>.<node_id>
        ib_ip = f"10.10.{team_id}.{node_id}"

        host_vars: dict[str, Any] = {
            "ansible_host": ip,
            "ansible_user": username,
            "node_id": node_id,
            "role": role,
            "ib_ip": ib_ip,
            "mac": mac,
            "gateway_ip": gateway_ip,
            "dns_ip": dns_ip,
            "mgmt_network": mgmt_network,
        }
        if bmc.get("ip"):
            host_vars["bmc_ip"] = bmc.get("ip")
        if bmc.get("user"):
            host_vars["bmc_user"] = bmc.get("user")

        hostvars[hostname] = host_vars
        cluster_hosts.append(hostname)

        if role == "headnode":
            headnodes.append(hostname)
        else:
            computenodes.append(hostname)

    if not headnode_host and cluster_hosts:
        headnode_host = cluster_hosts[0]

    all_vars: dict[str, Any] = {
        "team_id": team_id,
        "cluster_user": username,
        "cluster_user_home": f"/home/{username}",
        "cluster_domain": domain,
        "gateway_ip": gateway_ip,
        "dns_ip": dns_ip,
        "mgmt_network": mgmt_network,
        "ib_network": f"10.10.{team_id}.0/24",
        "ib_subnet_cidr": 24,
        "nfs_server_host": headnode_host or "node1",
        "nfs_server_ib_ip": f"10.10.{team_id}.1",
        "ansible_user": username,
        "ansible_ssh_private_key_file": "~/.ssh/carla_scc_ed25519",
        "ansible_python_interpreter": "/usr/bin/python3",
        "ansible_ssh_common_args": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
        "cluster_provider": provider,
    }

    inventory: dict[str, Any] = {
        "_meta": {
            "hostvars": hostvars,
        },
        "all": {
            "vars": all_vars,
            "children": ["cluster", "headnode", "computenode"],
        },
        "cluster": {
            "hosts": cluster_hosts,
        },
        "headnode": {
            "hosts": headnodes,
        },
        "computenode": {
            "hosts": computenodes,
        },
    }
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description="SCC CARLA Ansible Dynamic Inventory")
    parser.add_argument(
        "--list", action="store_true", help="List all hosts (JSON output)"
    )
    parser.add_argument("--host", type=str, help="Get specific host vars")
    parser.add_argument(
        "--manifest", type=str, help="Explicit path to cluster manifest or values.yaml"
    )
    args = parser.parse_args()

    manifest_path = _locate_manifest_path(args.manifest)
    if not manifest_path or not manifest_path.exists():
        if args.host:
            print(json.dumps({}))
        else:
            print(json.dumps({"_meta": {"hostvars": {}}, "all": {"children": []}}))
        return

    manifest_data = load_cluster_manifest(manifest_path)
    inv = build_inventory(manifest_data)

    if args.host:
        host_vars = inv.get("_meta", {}).get("hostvars", {}).get(args.host, {})
        print(json.dumps(host_vars, indent=2))
    else:
        print(json.dumps(inv, indent=2))


if __name__ == "__main__":
    main()
