"""Resolve the chosen bootstrap method into boot media without cache-based fallback."""

import hashlib
import json
from dataclasses import dataclass
from ipaddress import ip_network
from pathlib import Path

from cabrita.bootstrap import iso_builder
from cabrita.bootstrap.artifacts import ArtifactCache, artifact_lock
from cabrita.bootstrap.iso_builder import cached_build
from cabrita.core.bootstrap import BootstrapMethod
from cabrita.core.manifest import NodeSpec
from cabrita.core.resolved import ResolvedCluster
from cabrita.core.templating import TemplateEngine


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    source: Path
    auxiliary: Path | None = None


def prepare_media(
    cluster: ResolvedCluster,
    node: NodeSpec,
    cache: ArtifactCache,
    templates: TemplateEngine,
    public_key: str,
    work: Path,
) -> PreparedMedia:
    bootstrap = cluster.manifest.bootstrap
    assert bootstrap.artifact is not None
    base = cache.materialize(cluster.manifest.artifacts[bootstrap.artifact])
    if bootstrap.method == BootstrapMethod.CLOUD_INIT:
        return PreparedMedia(base)
    if bootstrap.method not in (
        BootstrapMethod.EMBEDDED_KICKSTART,
        BootstrapMethod.OEMDRV,
    ):
        raise ValueError(f"Unsupported media preparation: {bootstrap.method}")
    kickstart = render_kickstart(cluster, node, templates, public_key, work)
    digest = hashlib.sha256(
        json.dumps(
            {
                "base": cluster.manifest.artifacts[bootstrap.artifact].sha256,
                "method": bootstrap.method.value,
                "builder": hashlib.sha256(
                    Path(iso_builder.__file__).read_bytes()
                ).hexdigest(),
                "kickstart": kickstart.read_text(),
            }
        ).encode()
    ).hexdigest()
    target = cache.directory / f"{digest}.iso"
    # Each build has a unique input key; serialize publication for shared inputs.
    with artifact_lock(cache.directory / f"{digest}.build.lock"):
        cached_build(
            base,
            kickstart,
            target,
            embedded=bootstrap.method == BootstrapMethod.EMBEDDED_KICKSTART,
        )
        if bootstrap.method == BootstrapMethod.OEMDRV:
            return PreparedMedia(target, target.with_suffix(".img"))
    return PreparedMedia(target)


def render_kickstart(
    cluster: ResolvedCluster,
    node: NodeSpec,
    templates: TemplateEngine,
    public_key: str,
    work: Path,
) -> Path:
    bootstrap = cluster.manifest.bootstrap
    context = {
        **cluster.manifest.template_inputs,
        **bootstrap.inputs,
        "node_ip": node.ip,
        "netmask": str(ip_network(cluster.manifest.network.subnet).netmask),
        "gateway_ip": cluster.manifest.network.gateway,
        "dns_ip": cluster.manifest.network.dns,
        "hostname": node.hostname,
        "node_username": cluster.manifest.defaults.os.username,
        "pubkey": public_key,
        "target_disk": node.hardware.target_disk.removeprefix("/dev/")
        if node.hardware
        else ("vda" if node.vm is not None and node.vm.disk.bus == "virtio" else "sda"),
    }
    work.mkdir(parents=True, exist_ok=True)
    kickstart = work / "ks.cfg"
    content = (
        templates.render_string(bootstrap.kickstart.read_text(), context)
        if bootstrap.kickstart
        else templates.render("ks.cfg.j2", context)
    )
    kickstart.write_text(content)
    return kickstart
