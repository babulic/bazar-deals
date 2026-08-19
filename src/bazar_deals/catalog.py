from __future__ import annotations

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


def is_bulky(text: str) -> bool:
    hay = text.casefold()
    return any(keyword in hay for keyword in BULKY_KEYWORDS)
