"""Owned NAT networks and host-wide allocation without changing external networks."""

import hashlib
import json
import os
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from ipaddress import IPv4Network, ip_address, ip_network
from pathlib import Path
from typing import Any

from cabritactl.core.manifest.models import ClusterManifest

OWNER_NS = "https://cabritactl.org/network/1"


@contextmanager
def allocation_lock():
    # Linux abstract sockets are shared across users and released on process exit.
    with socket.socket(socket.AF_UNIX) as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                lock.bind("\0cabritactl-system-network-allocation-v1")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Another process holds the host network allocation lock"
                    ) from None
                time.sleep(0.1)
        yield


def host_routes() -> list[IPv4Network]:
    result = subprocess.run(
        ["ip", "-j", "-4", "route", "show", "table", "all"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    routes = []
    for item in json.loads(result.stdout):
        destination = item.get("dst", "default")
        if destination != "default":
            network = ip_network(destination, strict=False)
            if isinstance(network, IPv4Network):
                routes.append(network)
    return routes


def network_subnets(xml: str) -> list[IPv4Network]:
    result = []
    for element in ET.fromstring(xml).findall("ip"):
        if element.get("family", "ipv4") == "ipv4":
            prefix = element.get("prefix") or element.get("netmask")
            if prefix and element.get("address"):
                subnet = ip_network(f"{element.get('address')}/{prefix}", strict=False)
                if isinstance(subnet, IPv4Network):
                    result.append(subnet)
    return result


def select_subnet(occupied: list[IPv4Network]) -> IPv4Network:
    for subnet in IPv4Network("192.168.0.0/16").subnets(new_prefix=24):
        if not any(subnet.overlaps(other) for other in occupied):
            return subnet
    raise ValueError(
        "No free /24 in 192.168.0.0/16; specify a different managed subnet"
    )


def author_network(conn: Any, name: str) -> dict[str, Any]:
    with allocation_lock():
        occupied = host_routes()
        for network in conn.listAllNetworks():
            occupied.extend(network_subnets(network.XMLDesc(0)))
        subnet = select_subnet(occupied)
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return {
        "managed": True,
        "network_name": f"cabrita-{name}",
        "bridge": f"cbr{digest}",
        "subnet": str(subnet),
        "gateway": str(subnet[1]),
        "dns": str(subnet[1]),
    }


def validate_network(manifest: ClusterManifest) -> None:
    net = manifest.network
    subnet = ip_network(net.subnet)
    gateway = ip_address(net.gateway)
    if gateway not in subnet or gateway in (
        subnet.network_address,
        subnet.broadcast_address,
    ):
        raise ValueError("Gateway must be a usable address in the declared subnet")
    for node in manifest.nodes:
        address = ip_address(node.ip)
        if address not in subnet or address in (
            subnet.network_address,
            subnet.broadcast_address,
            gateway,
        ):
            raise ValueError(f"Invalid address for {node.hostname}: {address}")
        if net.managed and 2 <= int(address) - int(subnet.network_address) <= 99:
            raise ValueError(
                f"{node.hostname} overlaps the managed DHCP range (.2–.99)"
            )
    if net.managed and (not isinstance(subnet, IPv4Network) or subnet.prefixlen != 24):
        raise ValueError("Managed NAT networks currently require an IPv4 /24")
    if net.managed and (
        len(net.bridge) > 15 or not net.bridge.replace("-", "").isalnum()
    ):
        raise ValueError(
            "Managed bridge names must be alphanumeric/hyphen and at most 15 characters"
        )


def owned(xml: str, manifest: ClusterManifest) -> bool:
    owner = ET.fromstring(xml).find(f"metadata/{{{OWNER_NS}}}owner")
    return (
        owner is not None
        and owner.get("cluster") == manifest.name
        and owner.get("uid") == str(os.getuid())
    )


def check_network(conn: Any, manifest: ClusterManifest) -> None:
    validate_network(manifest)
    net = manifest.network
    networks = conn.listAllNetworks()
    matching = next((n for n in networks if n.name() == net.network_name), None)
    if not net.managed:
        if net.bridge != "virbr0":
            if not Path("/sys/class/net", net.bridge).exists():
                raise ValueError(f"External bridge {net.bridge} does not exist")
            return
        if matching is None or not matching.isActive():
            raise ValueError(
                f"External network {net.network_name} is missing or inactive"
            )
        xml = ET.fromstring(matching.XMLDesc(0))
        if ip_network(net.subnet) not in network_subnets(matching.XMLDesc(0)):
            raise ValueError("Manifest subnet differs from external network")
        for node in manifest.nodes:
            for r in xml.findall("./ip/dhcp/range"):
                if (
                    int(ip_address(r.attrib["start"]))
                    <= int(ip_address(node.ip))
                    <= int(ip_address(r.attrib["end"]))
                ):
                    reservation = xml.find(f"./ip/dhcp/host[@ip='{node.ip}']")
                    if (
                        reservation is None
                        or reservation.get("mac", "").lower()
                        != (node.mac or "").lower()
                    ):
                        raise ValueError(
                            f"Static address {node.ip} overlaps external DHCP; reserve it or use an address outside the range"
                        )
        return
    if matching is not None:
        xml = matching.XMLDesc(0)
        if not owned(xml, manifest):
            raise ValueError(
                f"Network {net.network_name} exists but is not owned by this cluster/user"
            )
        root = ET.fromstring(xml)
        bridge = root.find("bridge")
        if (
            network_subnets(xml) != [ip_network(net.subnet)]
            or bridge is None
            or bridge.get("name") != net.bridge
        ):
            raise ValueError(
                "Existing managed network differs from manifest; destroy it before changing topology"
            )
    occupied = []
    for n in networks:
        if n.name() != net.network_name:
            occupied.extend(network_subnets(n.XMLDesc(0)))
            bridge = ET.fromstring(n.XMLDesc(0)).find("bridge")
            if bridge is not None and bridge.get("name") == net.bridge:
                raise ValueError(f"Bridge {net.bridge} is used by {n.name()}")
    # Ignore only routes belonging to our already-defined bridge.
    routes = subprocess.run(
        ["ip", "-j", "-4", "route", "show", "table", "all"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    for route in json.loads(routes.stdout):
        if route.get("dst", "default") != "default" and not (
            matching is not None and route.get("dev") == net.bridge
        ):
            occupied.append(ip_network(route["dst"], strict=False))
    if any(ip_network(net.subnet).overlaps(other) for other in occupied):
        raise ValueError(
            f"Subnet {net.subnet} overlaps a host route or another network; choose another subnet and node addresses"
        )


def ensure_network(conn: Any, manifest: ClusterManifest) -> None:
    with allocation_lock():
        check_network(conn, manifest)
        if not manifest.network.managed:
            return
        net = manifest.network
        existing = next(
            (n for n in conn.listAllNetworks() if n.name() == net.network_name), None
        )
        if existing is None:
            root = ET.Element("network")
            ET.SubElement(root, "name").text = net.network_name
            ET.SubElement(
                ET.SubElement(root, "metadata"),
                f"{{{OWNER_NS}}}owner",
                cluster=manifest.name,
                uid=str(os.getuid()),
            )
            ET.SubElement(root, "forward", mode="nat")
            ET.SubElement(root, "bridge", name=net.bridge, stp="on", delay="0")
            subnet = ip_network(net.subnet)
            ip = ET.SubElement(
                root, "ip", address=net.gateway, netmask=str(subnet.netmask)
            )
            dhcp = ET.SubElement(ip, "dhcp")
            ET.SubElement(dhcp, "range", start=str(subnet[2]), end=str(subnet[99]))
            for node in manifest.nodes:
                ET.SubElement(
                    dhcp, "host", mac=node.mac or "", name=node.hostname, ip=node.ip
                )
            existing = conn.networkDefineXML(ET.tostring(root, encoding="unicode"))
        if not existing.isActive():
            existing.create()
        existing.setAutostart(1)


def remove_network(conn: Any, manifest: ClusterManifest) -> None:
    if not manifest.network.managed:
        return
    with allocation_lock():
        net = next(
            (
                n
                for n in conn.listAllNetworks()
                if n.name() == manifest.network.network_name
            ),
            None,
        )
        if net is None:
            return
        if not owned(net.XMLDesc(0), manifest):
            raise ValueError("Refusing to remove an unowned network")
        for domain in conn.listAllDomains():
            for source in ET.fromstring(domain.XMLDesc(0)).findall(
                "./devices/interface/source"
            ):
                if (
                    source.get("network") == net.name()
                    or source.get("bridge") == manifest.network.bridge
                ):
                    return  # Partial destroy or another domain still uses it.
        if net.isActive():
            net.destroy()
        net.undefine()
