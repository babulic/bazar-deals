"""Only functional, undamaged goods. Phrases live in data/bazar.yaml."""

from __future__ import annotations

import re
import unicodedata

from bazar_deals.domain import Condition, Listing
from bazar_deals.rules import rules

MIN_BATTERY_HEALTH_PERCENT = 84


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def is_damaged_text(text: str) -> bool:
    cfg = rules()["working"]
    hay = f" {_fold(text)} "
    if any(marker in hay for marker in cfg["always_damage"]):
        return True
    if any(marker in hay for marker in cfg["negated_phrases"]):
        return False
    return any(marker in hay for marker in cfg["damage_phrases"])


def battery_health(text: str) -> int | None:
    """Return explicitly stated battery health; unknown remains allowed."""
    folded = _fold(text)
    patterns = (
        r"(?:battery\s*health|battery|baterie|bateria|akku|kondicia\s*baterie)[^\d]{0,20}(\d{2,3})\s*%",
        r"(\d{2,3})\s*%[^\n]{0,20}(?:battery|baterie|bateria|akku)",
    )
    for pattern in patterns:
        match = re.search(pattern, folded, flags=re.I)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 100:
                return value
    return None


def is_working_listing(listing: Listing) -> bool:
    if listing.condition is Condition.FOR_PARTS:
        return False
    text = f"{listing.title} {listing.description} {_raw_text(listing.raw)}"
    health = battery_health(text)
    if health is not None and health < MIN_BATTERY_HEALTH_PERCENT:
        return False
    return not is_damaged_text(text)


def _raw_text(value: object, depth: int = 0) -> str:
    """Flatten marketplace condition fields for the safety gate."""
    if depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_raw_text(item, depth + 1)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_raw_text(item, depth + 1) for item in value)
    return ""
