import os
import re
from pathlib import Path
from typing import Any

import yaml

from cabrita.core.manifest.models import ClusterManifest, HardwareSpec, NodeSpec, VMSpec

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-(.*?))?\}")


def _merge_defaults(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in overrides.items():
        match result.get(key), value:
            case dict() as inherited, dict() as override:
                result[key] = _merge_defaults(inherited, override)
            case _:
                result[key] = value
    return result


def interpolate_env_vars(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_val = match.group(2) or ""
        return os.environ.get(var_name, default_val)

    return _ENV_PATTERN.sub(_replace, text)


def parse_manifest(raw_text: str) -> ClusterManifest:
    """Parses and resolves a ClusterManifest from a YAML string."""
    interpolated_text = interpolate_env_vars(raw_text)
    data: dict[str, Any] = yaml.safe_load(interpolated_text) or {}

    manifest = ClusterManifest.model_validate(data)

    # Resolve node defaults inheritance
    defaults = manifest.defaults
    resolved_nodes: list[NodeSpec] = []
    for node in manifest.nodes:
        node_dict = node.model_dump()
        if manifest.provider == "libvirt":
            base_vm = defaults.vm.model_dump()
            if node.vm is not None:
                base_vm = _merge_defaults(
                    base_vm, node.vm.model_dump(exclude_unset=True)
                )
            node_dict["vm"] = VMSpec.model_validate(base_vm)

        if manifest.provider == "helvetios":
            base_hw = defaults.hardware.model_dump()
            if node.hardware is not None:
                base_hw.update(node.hardware.model_dump(exclude_unset=True))
            node_dict["hardware"] = HardwareSpec.model_validate(base_hw)

        resolved_nodes.append(NodeSpec.model_validate(node_dict))

    manifest_dict = manifest.model_dump()
    manifest_dict["nodes"] = [n.model_dump() for n in resolved_nodes]
    return ClusterManifest.model_validate(manifest_dict)


def load_manifest(manifest_path: Path | str) -> ClusterManifest:
    """Loads and validates a cluster manifest from a file path."""
    path = Path(manifest_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Cluster manifest not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    return parse_manifest(raw_text)
