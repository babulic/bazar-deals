from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PACKAGE_YAML = Path(__file__).resolve().parent / "data" / "bazar.yaml"


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    with _PACKAGE_YAML.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
