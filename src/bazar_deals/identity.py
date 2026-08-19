from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from bazar_deals.domain import IdentifiedItem, Listing, Vertical

_GENERIC_BRANDS = frozenset(
    {
        "commodore",
        "nintendo",
        "sony",
        "apple",
        "samsung",
        "xiaomi",
        "huawei",
        "canon",
        "nikon",
        "iphone",
        "ipad",
        "macbook",
        "playstation",
        "xbox",
        "atari",
        "sega",
        "c64",
        "c128",
    }
)

_MEDIA = (
    "kazeta",
    "cassette",
    "tape",
    "páska",
    "paska",
    "hra",
    "hry",
    "game",
    "games",
    "cartridge",
    "kartridž",
    "kartridz",
    "manual",
    "manuál",
    "kniha",
    "poster",
    "nálepka",
    "nalepka",
    "sticker",
    "stickers",
    "náhrada",
    "nahrada",
    "obal",
    "case",
    "cover",
    "pre c64",
    "pro c64",
    "for c64",
    "für c64",
    "fur c64",
    "c64/c128",
    "c64-c128",
    "c64 c128",
    "c64/128",
    "konami",
)

_HARDWARE = (
    "počítač",
    "pocitac",
    "computer",
    "motherboard",
    "základná doska",
    "zakladna doska",
    "disk drive",
    "mechanika",
    "unlocked",
    "breadbin",
)

_STOP = frozenset(
    {
        "the",
        "and",
        "und",
        "der",
        "die",
        "das",
        "ein",
        "mit",
        "für",
        "fur",
        "von",
        "pre",
        "pri",
        "na",
        "zo",
        "za",
        "od",
        "do",
        "ako",
        "new",
        "neu",
        "novy",
        "nový",
        "used",
        "gebraucht",
        "ebay",
        "aukro",
        "vinted",
        "bazos",
    }
)


class ItemKind(StrEnum):
    MEDIA = "media"
    HARDWARE = "hardware"
    GENERIC = "generic"


def identify(listing: Listing, vertical_hint: Vertical | None = None) -> IdentifiedItem:
    hay = f"{listing.title} {listing.description}"
    kind = classify_kind(hay)
    query = sold_query(hay, kind)
    weak = query is None
    return IdentifiedItem(
        listing=listing,
        vertical=vertical_hint,
        canonical_name=listing.title.strip(),
        model=query,
        search_query=query or "",
        kind=kind.value,
        confidence=0.15 if weak else (0.62 if kind is ItemKind.GENERIC else 0.78),
    )


def classify_kind(text: str) -> ItemKind:
    hay = _fold(text)
    media = any(marker in hay for marker in _MEDIA)
    hardware = any(marker in hay for marker in _HARDWARE)
    if media:
        return ItemKind.MEDIA
    if hardware:
        return ItemKind.HARDWARE
    return ItemKind.GENERIC


def sold_query(text: str, kind: ItemKind | None = None) -> str | None:
    """Tight sold-search phrase. Weak titles return None → no BUY."""
    kind = kind or classify_kind(text)
    tokens = significant_tokens(text)
    if kind is ItemKind.MEDIA:
        distinctive = [tok for tok in tokens if tok not in _GENERIC_BRANDS]
        if len(distinctive) < 2:
            return None
        return " ".join(distinctive[:6])
    if len(tokens) < 2:
        return None
    return " ".join(tokens[:6])


def significant_tokens(text: str) -> list[str]:
    folded = _fold(text)
    raw = re.findall(r"[a-z0-9]+", folded)
    out: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in _STOP or token in seen:
            continue
        if len(token) >= 3 or (token.isdigit() and len(token) >= 2):
            seen.add(token)
            out.append(token)
    return out


def similar_titles(left: str, right: str) -> bool:
    if classify_kind(left) != classify_kind(right):
        return False
    a = set(significant_tokens(left))
    b = set(significant_tokens(right))
    if not a or not b:
        return False
    inter = a & b
    union = a | b
    jaccard = len(inter) / len(union)
    if classify_kind(left) is ItemKind.MEDIA:
        return len(inter - _GENERIC_BRANDS) >= 2 and jaccard >= 0.25
    return len(inter) >= 2 and jaccard >= 0.3


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))
