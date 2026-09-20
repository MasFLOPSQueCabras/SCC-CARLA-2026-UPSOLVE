import os
import re
from pathlib import Path
from typing import Any

import yaml

from scc_core.manifest.models import ClusterManifest, HardwareSpec, NodeSpec, VMSpec

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-(.*?))?\}")


def interpolate_env_vars(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_val)

    return _ENV_PATTERN.sub(_replace, text)


def load_manifest(manifest_path: Path | str) -> ClusterManifest:
    path = Path(manifest_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Cluster manifest not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    interpolated_text = interpolate_env_vars(raw_text)
    data: dict[str, Any] = yaml.safe_load(interpolated_text) or {}

    manifest = ClusterManifest.model_validate(data)

    # Resolve node defaults inheritance
    defaults = manifest.defaults
    resolved_nodes: list[NodeSpec] = []
    for node in manifest.nodes:
        node_dict = node.model_dump()
        if manifest.provider in ("libvirt", "vm"):
            base_vm = defaults.vm.model_dump()
            if node.vm is not None:
                base_vm.update(node.vm.model_dump(exclude_unset=True))
            node_dict["vm"] = VMSpec.model_validate(base_vm)

        if manifest.provider in ("helvetios", "bmc"):
            base_hw = defaults.hardware.model_dump()
            if node.hardware is not None:
                base_hw.update(node.hardware.model_dump(exclude_unset=True))
            node_dict["hardware"] = HardwareSpec.model_validate(base_hw)

        resolved_nodes.append(NodeSpec.model_validate(node_dict))

    manifest_dict = manifest.model_dump()
    manifest_dict["nodes"] = [n.model_dump() for n in resolved_nodes]
    return ClusterManifest.model_validate(manifest_dict)
