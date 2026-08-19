from __future__ import annotations

from bazar_deals.catalog import CATALOG_COMPS, VERTICAL_KEYWORDS
from bazar_deals.domain import IdentifiedItem, Listing, Vertical


def identify(listing: Listing, vertical_hint: Vertical | None = None) -> IdentifiedItem:
    hay = f"{listing.title} {listing.description}".casefold()
    catalog_hit = _catalog_match(hay)
    vertical = vertical_hint or _guess_vertical(hay)
    if catalog_hit:
        canonical, _ = CATALOG_COMPS[catalog_hit]
        return IdentifiedItem(
            listing=listing,
            vertical=vertical,
            canonical_name=canonical,
            model=canonical,
            confidence=0.86,
        )
    return IdentifiedItem(
        listing=listing,
        vertical=vertical,
        canonical_name=listing.title.strip(),
        confidence=0.35 if vertical else 0.15,
    )


def _catalog_match(hay: str) -> str | None:
    for key in sorted(CATALOG_COMPS, key=len, reverse=True):
        if key in hay:
            return key
    return None


def _guess_vertical(hay: str) -> Vertical | None:
    for vertical, keywords in VERTICAL_KEYWORDS.items():
        if any(keyword in hay for keyword in keywords):
            return vertical
    return None
