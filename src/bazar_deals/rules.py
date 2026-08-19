from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PACKAGE_YAML = Path(__file__).resolve().parent / "data" / "bazar.yaml"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    with _PACKAGE_YAML.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    overlay = os.environ.get("BAZAR_CONFIG", "").strip()
    for path in (Path("bazar.yaml"), Path(overlay) if overlay else None):
        if path is not None and path.is_file():
            with path.open(encoding="utf-8") as handle:
                data = _deep_merge(data, yaml.safe_load(handle) or {})
    return data
