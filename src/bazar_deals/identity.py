from __future__ import annotations

import re
import unicodedata

from bazar_deals.domain import IdentifiedItem, ItemKind, Listing, Vertical
from bazar_deals.rules import rules
from bazar_deals.working import is_damaged_text


def _id() -> dict:
    return rules()["identity"]


def identify(listing: Listing, vertical_hint: Vertical | None = None) -> IdentifiedItem:
    hay = f"{listing.title} {listing.description}"
    conf = _id()["confidence"]
    if is_damaged_text(hay) or listing.condition.value == "for_parts":
        return IdentifiedItem(
            listing=listing,
            vertical=vertical_hint,
            canonical_name=listing.title.strip(),
            confidence=float(conf["damaged"]),
        )
    kind = classify_kind(hay)
    query = sold_query(hay, kind)
    weak = query is None
    if weak:
        score = float(conf["weak"])
    elif kind is ItemKind.GENERIC:
        score = float(conf["generic"])
    else:
        score = float(conf["known"])
    return IdentifiedItem(
        listing=listing,
        vertical=vertical_hint,
        canonical_name=listing.title.strip(),
        model=query,
        search_query=query or "",
        kind=kind.value,
        confidence=score,
    )


def classify_kind(text: str) -> ItemKind:
    hay = _fold(text)
    cfg = _id()
    markers = cfg["kind_markers"]
    for kind in cfg["kind_priority"]:
        if any(_has_marker(hay, marker) for marker in markers.get(kind, [])):
            return ItemKind(kind)
    return ItemKind.GENERIC


def sold_query(text: str, kind: ItemKind | None = None) -> str | None:
    cfg = _id()
    kind = kind or classify_kind(text)
    tokens = significant_tokens(text)
    brands = set(cfg["generic_brands"])
    take = int(cfg["sold_query_tokens"])
    loose = {ItemKind(name) for name in cfg.get("loose_kinds", ["media"])}
    if kind in loose:
        distinctive = [tok for tok in tokens if tok not in brands]
        if len(distinctive) < int(cfg["min_media_distinctive"]):
            return None
        return " ".join(distinctive[:take])
    if len(tokens) < int(cfg["min_tokens"]):
        return None
    return " ".join(tokens[:take])


def significant_tokens(text: str) -> list[str]:
    cfg = _id()
    stop = set(cfg["stop_words"])
    min_len = int(cfg["min_token_len"])
    min_digit = int(cfg["min_digit_len"])
    folded = _fold(text)
    raw = re.findall(r"[a-z0-9]+", folded)
    out: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in stop or token in seen:
            continue
        if len(token) >= min_len or (token.isdigit() and len(token) >= min_digit):
            seen.add(token)
            out.append(token)
    return out


def similar_titles(left: str, right: str) -> bool:
    cfg = _id()
    left_kind = classify_kind(left)
    right_kind = classify_kind(right)
    if left_kind != right_kind and left_kind is not ItemKind.GENERIC and right_kind is not ItemKind.GENERIC:
        return False
    a = set(significant_tokens(left))
    b = set(significant_tokens(right))
    if not a or not b:
        return False
    inter = a & b
    union = a | b
    jaccard = len(inter) / len(union)
    brands = set(cfg["generic_brands"])
    overlap = int(cfg["min_overlap"])
    kind = left_kind if left_kind is not ItemKind.GENERIC else right_kind
    if kind.value in cfg.get("loose_kinds", ["media"]):
        return len(inter - brands) >= overlap and jaccard >= float(cfg["media_jaccard"])
    return len(inter) >= overlap and jaccard >= float(cfg["title_jaccard"])


def _has_marker(hay: str, marker: str) -> bool:
    token = _fold(marker)
    if not token:
        return False
    return re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", hay) is not None


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))
