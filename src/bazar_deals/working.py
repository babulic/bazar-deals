"""Only functional, undamaged goods. Phrases live in data/bazar.yaml."""

from __future__ import annotations

import unicodedata

from bazar_deals.domain import Condition, Listing
from bazar_deals.rules import rules


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


def is_working_listing(listing: Listing) -> bool:
    if listing.condition is Condition.FOR_PARTS:
        return False
    return not is_damaged_text(f"{listing.title} {listing.description}")
