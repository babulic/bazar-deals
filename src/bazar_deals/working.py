"""Only functional, undamaged goods. For-parts / broken listings are out."""

from __future__ import annotations

import unicodedata

from bazar_deals.domain import Condition, Listing

_DAMAGE = (
    "na diely",
    "na suciastky",
    "for parts",
    "not working",
    "does not work",
    "doesn't work",
    "nefunkcny",
    "nefunkcne",
    "nefunkcna",
    "k oprave",
    "na opravu",
    "defektne",
    "defective",
    "beschadigt",
    "ersatzteil",
    "poskodene",
    "poskodeny",
    "poskodena",
    "rozbite",
    "rozbity",
    "broken screen",
    "cracked screen",
    "water damage",
    "nestartuje",
    "as-is",
    " as is ",
)

_NEGATED = (
    "bez defekt",
    "bez poskod",
    "no damage",
    "undamaged",
    "fully working",
    "tested working",
    "100% working",
)


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def is_damaged_text(text: str) -> bool:
    hay = f" {_fold(text)} "
    if "for parts" in hay or "na diely" in hay or "ersatzteil" in hay:
        return True
    if any(marker in hay for marker in _NEGATED):
        return False
    return any(marker in hay for marker in _DAMAGE)


def is_working_listing(listing: Listing) -> bool:
    if listing.condition is Condition.FOR_PARTS:
        return False
    return not is_damaged_text(f"{listing.title} {listing.description}")
