from __future__ import annotations

import os
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
CHRISTMAS_MARKERS = tuple(_catalog().get("christmas_markers") or ())
CHRISTMAS_LIGHT_PRODUCTS = tuple(_catalog().get("christmas_light_products") or ())
CHRISTMAS_LIGHTING_TERMS = tuple(_catalog().get("christmas_lighting_terms") or ())
MAX_WEIGHT_KG = float(rules()["hunt"].get("max_weight_kg", 2))
MAX_EDGE_CM = float(rules()["hunt"].get("max_edge_cm", 50))
MAX_SUM_CM = float(rules()["hunt"].get("max_sum_cm", 120))


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def hunt_research_only() -> bool:
    """Skip newest-dumps; search only the expanded SKU pack after a 0-result hunt."""
    return _truthy("BAZAR_HUNT_RESEARCH")


def hunt_expand() -> bool:
    return hunt_research_only() or _truthy("BAZAR_HUNT_EXPAND")


def _clean_queries(raw: object) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        query = str(item).strip()
        key = query.casefold()
        if len(query) < 2 or key in seen:
            continue
        seen.add(key)
        found.append(query)
    return found


def hunt_target_queries() -> tuple[str, ...]:
    """Product searches that can actually clear the 30 € net-profit floor."""
    hunt = rules()["hunt"]
    found = _clean_queries(hunt.get("target_queries"))
    if hunt_expand():
        extra = _clean_queries(hunt.get("expand_queries"))
        seen = {query.casefold() for query in found}
        for query in extra:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(query)
    return tuple(found)


def matches_hunt_target(text: str) -> bool:
    """True when the ad looks like a fast-moving SKU we actually hunt for."""
    hay = (text or "").casefold()
    if len(hay) < 3:
        return False
    return any(len(query) >= 3 and query.casefold() in hay for query in hunt_target_queries())


# Match "6 kg" / "6,5kg" but not storage like "16GB".
_WEIGHT_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)
# "1800 g" / "1800g" — not 16GB (digit+letter is one word).
_GRAM_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*g(?:ram(?:y|ov|ů)?)?\b", re.IGNORECASE)
# 50x40x30, 50 × 40 × 30 cm, 500x400x300 mm
_DIM_RE = re.compile(
    r"(?P<a>\d+(?:[.,]\d+)?)\s*(?:cm|mm)?\s*[x×*]\s*"
    r"(?P<b>\d+(?:[.,]\d+)?)\s*(?:cm|mm)?\s*[x×*]\s*"
    r"(?P<c>\d+(?:[.,]\d+)?)\s*(?P<unit>cm|mm)?",
    re.IGNORECASE,
)


def is_bulky(text: str) -> bool:
    hay = text.casefold()
    return any(keyword in hay for keyword in BULKY_KEYWORDS)


def is_christmas_lighting(text: str) -> bool:
    """Seasonal Christmas lights only. Headlamps and ordinary lighting stay in."""
    hay = text.casefold()
    if any(product in hay for product in CHRISTMAS_LIGHT_PRODUCTS):
        return True
    has_season = any(marker in hay for marker in CHRISTMAS_MARKERS)
    has_light = any(term in hay for term in CHRISTMAS_LIGHTING_TERMS)
    return has_season and has_light


def is_skip_keyword(text: str) -> bool:
    return is_christmas_lighting(text)


def stated_weight_kg(text: str) -> float | None:
    blob = text or ""
    weights = [
        float(match.group(1).replace(",", "."))
        for match in _WEIGHT_RE.finditer(blob)
    ]
    grams = [
        float(match.group(1).replace(",", ".")) / 1000.0
        for match in _GRAM_RE.finditer(blob)
    ]
    found = weights + grams
    return max(found) if found else None


def is_too_heavy(text: str, *, max_kg: float | None = None) -> bool:
    limit = MAX_WEIGHT_KG if max_kg is None else max_kg
    weight = stated_weight_kg(text)
    return weight is not None and weight > limit


def _to_cm(value: float, *, millimetres: bool) -> float:
    return value / 10.0 if millimetres else value


def stated_box_cm(text: str) -> tuple[float, float, float] | None:
    """L×W×H in centimetres when the ad states a three-side size."""
    blob = text or ""
    match = _DIM_RE.search(blob)
    if not match:
        return None
    a = float(match.group("a").replace(",", "."))
    b = float(match.group("b").replace(",", "."))
    c = float(match.group("c").replace(",", "."))
    unit = (match.group("unit") or "").casefold()
    window = blob[max(0, match.start() - 16) : match.end() + 16].casefold()
    millimetres = unit == "mm" or (not unit and "mm" in window and "cm" not in window)
    if not unit and not millimetres and max(a, b, c) > MAX_SUM_CM:
        millimetres = True
    return (_to_cm(a, millimetres=millimetres), _to_cm(b, millimetres=millimetres), _to_cm(c, millimetres=millimetres))


def is_oversized(text: str) -> bool:
    """True when stated size exceeds the shoebox: longest edge 50 cm, L+W+H 120 cm."""
    box = stated_box_cm(text)
    if box is None:
        return False
    return max(box) > MAX_EDGE_CM or sum(box) > MAX_SUM_CM


def reject_physical(text: str) -> str | None:
    """Funnel key if the listing is not a small, shippable, shoebox-scale item."""
    if is_bulky(text):
        return "bulky"
    if is_christmas_lighting(text):
        return "skip_keyword"
    if is_too_heavy(text):
        return "heavy"
    if is_oversized(text):
        return "oversized"
    return None
