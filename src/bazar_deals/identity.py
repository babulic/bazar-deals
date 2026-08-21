from __future__ import annotations

import re
import unicodedata

from bazar_deals.domain import IdentifiedItem, ItemKind, Listing, Vertical
from bazar_deals.rules import rules
from bazar_deals.working import is_damaged_text


def _id() -> dict:
    return rules()["identity"]


_REPLACEMENT_PART_PATTERNS = (
    r"\b(?:replacement|spare\s+part|ersatz(?:teil)?|nahradn\w*\s+diel|náhradn\w*\s+diel)\b",
    r"\b(?:display|displej|lcd|oled|digitizer|touchscreen|dotykov\w*\s+sklo)\b.{0,30}\b(?:pre|pro|for|fur|für|na)\b.{0,25}\b(?:iphone|ipad|galaxy|pixel|apple|samsung)\b",
    r"\b(?:novy|nový|new|neu)\s+(?:display|displej|lcd|oled|digitizer|touchscreen)\b",
    r"\b(?:back\s+glass|zadn\w*\s+sklo|housing|charging\s+port|nabijac\w*\s+konektor|nabíjac\w*\s+konektor|flex\s+(?:cable|kabel)|camera\s+module|logic\s+board)\b",
    r"\b(?:battery|bateria|batéria|akku)\b.{0,20}\b(?:pre|pro|for|fur|für)\b.{0,20}\b(?:iphone|ipad|galaxy|pixel)\b",
    r"\b(?:iphone|ipad|galaxy|pixel)\b.{0,25}\b(?:lcd|oled|digitizer|touchscreen|replacement|ersatzteil)\b",
)


def is_replacement_part_text(text: str) -> bool:
    folded = _fold(text)
    return any(re.search(pattern, folded, flags=re.I) for pattern in _REPLACEMENT_PART_PATTERNS)


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
    # Title-level replacement-part detection has priority over a phone/model name.
    if is_replacement_part_text(listing.title):
        kind = ItemKind.ACCESSORIES
    else:
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
    if is_replacement_part_text(text):
        return ItemKind.ACCESSORIES
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


def _storage_tokens(text: str) -> set[str]:
    folded = _fold(text)
    out = {f"{size}gb" for size in re.findall(r"\b(16|32|64|128|256|512)\s*(?:gb|g)\b", folded)}
    out.update(f"{size}tb" for size in re.findall(r"\b([1248])\s*tb\b", folded))
    return out


def _years(text: str) -> set[str]:
    return set(re.findall(r"\b20(?:1[0-9]|2[0-9])\b", _fold(text)))


def _phone_signature(text: str) -> str | None:
    folded = _fold(text)
    patterns = (
        r"\biphone\s+(se(?:\s*(?:2020|2022))?|\d{1,2})\b",
        r"\bgalaxy\s+(s\d{1,2}|a\d{1,2}|note\s*\d{1,2}|z\s*(?:fold|flip)\s*\d?)\b",
        r"\bpixel\s+(\d{1,2}[a-z]?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, folded)
        if match:
            return re.sub(r"\s+", "", match.group(0))
    return None


def _variant_tokens(text: str) -> set[str]:
    folded = _fold(text)
    variants = {"pro", "max", "plus", "mini", "ultra", "fe"}
    return {token for token in variants if re.search(rf"\b{token}\b", folded)}


def _hard_specs_match(left: str, right: str, kind: ItemKind) -> bool:
    """Reject comps that differ on price-critical model/spec tokens."""
    # A replacement part must never be priced from complete-product comps, or vice versa.
    if is_replacement_part_text(left) != is_replacement_part_text(right):
        return False

    left_storage = _storage_tokens(left)
    right_storage = _storage_tokens(right)
    if left_storage:
        if not right_storage or left_storage != right_storage:
            return False

    left_years = _years(left)
    right_years = _years(right)
    if left_years and (not right_years or left_years != right_years):
        return False

    if kind is ItemKind.PHONES:
        left_phone = _phone_signature(left)
        right_phone = _phone_signature(right)
        if left_phone and left_phone != right_phone:
            return False
        if _variant_tokens(left) != _variant_tokens(right):
            return False
    return True


def similar_titles(left: str, right: str) -> bool:
    cfg = _id()
    left_kind = classify_kind(left)
    right_kind = classify_kind(right)
    if left_kind != right_kind and left_kind is not ItemKind.GENERIC and right_kind is not ItemKind.GENERIC:
        return False
    kind = left_kind if left_kind is not ItemKind.GENERIC else right_kind
    if not _hard_specs_match(left, right, kind):
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
