from __future__ import annotations

import re

from bazar_deals.domain import Vertical
from bazar_deals.rules import rules


def _catalog() -> dict:
    return rules()["catalog"]


def _rubs(codes: list[str]) -> tuple[dict[str, str], ...]:
    return tuple({"rub": code} for code in codes)


BAZOS_RSS = dict(_catalog()["bazos_rss"])
SMALL_BAZOS_RUBS = _rubs(_catalog()["small_bazos_rubs"])
VERTICAL_RSS = {
    Vertical(name): _rubs(codes) for name, codes in _catalog()["vertical_rss"].items()
}
VERTICAL_KEYWORDS = {
    Vertical(name): tuple(words) for name, words in _catalog()["vertical_keywords"].items()
}
BULKY_KEYWORDS = tuple(_catalog()["bulky_keywords"])
SKIP_KEYWORDS = tuple(_catalog().get("skip_keywords") or ())
MAX_WEIGHT_KG = float(rules()["hunt"].get("max_weight_kg", 5))

# Match "6 kg" / "6,5kg" but not storage like "16GB".
_WEIGHT_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)


def is_bulky(text: str) -> bool:
    hay = text.casefold()
    return any(keyword in hay for keyword in BULKY_KEYWORDS)


def is_skip_keyword(text: str) -> bool:
    hay = text.casefold()
    return any(keyword in hay for keyword in SKIP_KEYWORDS)


def stated_weight_kg(text: str) -> float | None:
    weights = [
        float(match.group(1).replace(",", "."))
        for match in _WEIGHT_RE.finditer(text or "")
    ]
    return max(weights) if weights else None


def is_too_heavy(text: str, *, max_kg: float | None = None) -> bool:
    limit = MAX_WEIGHT_KG if max_kg is None else max_kg
    weight = stated_weight_kg(text)
    return weight is not None and weight > limit


def reject_physical(text: str) -> str | None:
    """Funnel key if the listing is not a small, shippable, shoebox-scale item."""
    if is_bulky(text):
        return "bulky"
    if is_skip_keyword(text):
        return "skip_keyword"
    if is_too_heavy(text):
        return "heavy"
    return None
