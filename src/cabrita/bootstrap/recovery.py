"""Prepare one recovery program for virtual and physical machines."""

import base64
import json
import platform
from ipaddress import ip_address, ip_network
from pathlib import Path
from uuid import uuid4

from cabrita.bootstrap import restore_runtime
from cabrita.bootstrap.artifacts import ArtifactCache
from cabrita.bootstrap.golden import GoldenMetadata
from cabrita.core.manifest import NodeSpec
from cabrita.core.resolved import ResolvedCluster


def load_metadata(
    cluster: ResolvedCluster, node: NodeSpec, cache: ArtifactCache
) -> GoldenMetadata:
    bootstrap = cluster.manifest.bootstrap
    assert bootstrap.metadata is not None and bootstrap.payload is not None
    metadata = GoldenMetadata.model_validate_json(
        cache.materialize(cluster.manifest.artifacts[bootstrap.metadata]).read_text()
    )
    payload = cluster.manifest.artifacts[bootstrap.payload]
    if metadata.sha256 != payload.sha256.lower():
        raise ValueError("Golden metadata and payload checksums differ")
    if node.vm is not None:
        if node.vm.firmware != metadata.firmware:
            raise ValueError("Golden image firmware mismatch")
        if node.vm.disk.size_gb * 1024**3 < metadata.disk_bytes:
            raise ValueError("Target disk is smaller than the golden image")
        if platform.machine() != metadata.architecture:
            raise ValueError("Golden image architecture mismatch")
    return metadata


def render_recovery(
    cluster: ResolvedCluster,
    node: NodeSpec,
    cache: ArtifactCache,
    public_key: str,
    work: Path,
    payload_url: str,
) -> Path:
    metadata = load_metadata(cluster, node, cache)
    network = cluster.manifest.network
    subnet = ip_network(network.subnet)
    for address in (node.ip, network.gateway, network.dns):
        ip_address(address)
    disk = (
        node.hardware.target_disk
        if node.hardware
        else (
            "/dev/vda"
            if node.vm is not None and node.vm.disk.bus == "virtio"
            else "/dev/sda"
        )
    )
    identity = {
        "token": uuid4().hex,
        "hostname": node.hostname,
        "username": cluster.manifest.defaults.os.username,
        "public_key": public_key,
        "mac": node.mac,
        "ip": node.ip,
        "prefix": subnet.prefixlen,
        "gateway": network.gateway,
        "dns": network.dns,
        "payload_sha256": metadata.sha256,
    }
    configuration = {
        "metadata": metadata.model_dump(),
        "identity": identity,
        "target_disk": disk,
        "payload_url": payload_url,
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "restore.json").write_text(json.dumps(configuration, indent=2))
    encoded = base64.b64encode(json.dumps(configuration).encode()).decode()
    script = Path(restore_runtime.__file__).read_text()
    kickstart = work / "ks.cfg"
    kickstart.write_text(
        "#version=RHEL10\ncmdline\npoweroff\nlang en_US.UTF-8\nkeyboard us\nrootpw --lock\n"
        + "%pre --interpreter=/usr/bin/python3 --erroronfail --log=/tmp/cabrita-restore.log\n"
        + script
        + "\nimport base64, traceback\ntry:\n    restore(json.loads(base64.b64decode("
        + repr(encoded)
        + ")))\nexcept BaseException:\n    with open('/dev/console', 'w') as console:\n        traceback.print_exc(file=console)\n    raise\n%end\n"
    )
    return kickstart
