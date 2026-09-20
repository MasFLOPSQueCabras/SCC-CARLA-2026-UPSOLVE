from pathlib import Path
from typing import Any

import yaml

from scc_core.manifest.loader import interpolate_env_vars


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = base.copy()
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_values(values_path: Path | str) -> dict[str, Any]:
    path = Path(values_path).expanduser().resolve()
    if not path.exists():
        return {}
    raw_text = path.read_text(encoding="utf-8")
    interpolated = interpolate_env_vars(raw_text)
    data = yaml.safe_load(interpolated)
    return data if isinstance(data, dict) else {}
