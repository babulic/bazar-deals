from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict

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


# Fields beyond title and description that still describe the goods. eBay puts
# specs in shortDescription and localizedAspects, Aukro in the category path,
# Vinted in brand/size. Nested `detail` copies from marketplace APIs count too.
_RAW_TEXT_FIELDS = (
    "shortDescription",
    "subtitle",
    "condition",
    "conditionDescription",
    "brand",
    "brand_title",
    "size",
    "size_title",
    "categoryPath",
    "itemName",
    "rss_title",
    "manufacturer",
    "mpn",
    "model",
    "color",
    "material",
    "product",
)
_NESTED_RAW = ("detail", "product", "item")
_ASPECT_KEYS = ("localizedAspects", "aspects", "itemSpecifics", "attributes")
# Seller-home country names appear on almost every ad and are not the origin
# of the specimen. Mineral deposit names and foreign origins are price-critical.
_HOME_ORIGINS = frozenset({"slovensko"})


def _flatten(value: object, depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _flatten(item, depth + 1)]
    if isinstance(value, (list, tuple)):
        return [part for item in value for part in _flatten(item, depth + 1)]
    return []


# Bazos RSS ships the thumbnail as a raw <img src="https://..."> in the body,
# which otherwise contributes img, src, https and the image id to the identity.
# A letter has to follow the bracket, so "rozmer < 7 cm" survives intact.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")
_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)
_ENTITY_RE = re.compile(r"&(?:[a-z]{2,8}|#\d{1,5});", re.IGNORECASE)


def strip_markup(text: str) -> str:
    without_tags = _HTML_TAG_RE.sub(" ", text or "")
    return _ENTITY_RE.sub(" ", _URL_RE.sub(" ", without_tags))


def listing_text(listing: Listing) -> str:
    """Every field that can say what the item is, not just the title.

    Sellers routinely leave the capacity, the production year or the part number
    out of the title and state it only in the body of the ad. Marketplace APIs
    keep the same facts in structured fields (eBay item specifics, Aukro
    category path, Vinted brand) which the headline never repeats.
    """
    parts = [listing.title, listing.description]
    raw = listing.raw if isinstance(listing.raw, dict) else {}
    blobs = [raw]
    for nest in _NESTED_RAW:
        nested = raw.get(nest)
        if isinstance(nested, dict):
            blobs.append(nested)
    for blob in blobs:
        for key in _RAW_TEXT_FIELDS:
            if key in blob:
                parts.extend(_flatten(blob[key]))
        parts.extend(_aspect_text(blob))
    return strip_markup(" ".join(part for part in parts if part))


def _aspect_text(blob: dict) -> list[str]:
    """Turn eBay-style name/value item specifics into searchable prose."""
    parts: list[str] = []
    for key in _ASPECT_KEYS:
        value = blob.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("localizedName") or ""
                    raw_value = (
                        item.get("value")
                        or item.get("localizedValue")
                        or item.get("values")
                    )
                    if isinstance(raw_value, list):
                        raw_value = " ".join(str(part) for part in raw_value if part)
                    if raw_value:
                        parts.append(f"{name} {raw_value}".strip())
                elif isinstance(item, str) and item.strip():
                    parts.append(item)
        elif isinstance(value, dict):
            for name, raw_value in value.items():
                if isinstance(raw_value, list):
                    raw_value = " ".join(str(part) for part in raw_value if part)
                if raw_value:
                    parts.append(f"{name} {raw_value}".strip())
    return parts


def identify(listing: Listing, vertical_hint: Vertical | None = None) -> IdentifiedItem:
    hay = listing_text(listing)
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
    specs = extract_specs(hay)
    query = sold_query(hay, kind)
    if query is not None:
        query = with_specs(query, specs)
    weak = query is None
    if weak:
        score = float(conf["weak"])
    elif kind is ItemKind.GENERIC:
        score = float(conf["generic"])
    else:
        score = float(conf["known"])
    # A headline like "Predám telefón" is a poor name for alerts and for
    # matching sold comps. Prefer the product identity mined from the body.
    canonical = listing.title.strip()
    if query and sold_query(listing.title, kind) is None:
        canonical = query
    return IdentifiedItem(
        listing=listing,
        vertical=vertical_hint,
        canonical_name=canonical,
        model=query,
        search_query=query or "",
        kind=kind.value,
        specs=specs,
        confidence=score,
    )


def with_specs(query: str, specs: ItemSpecs | None) -> str:
    """Append decisive spec tokens the token-frequency query dropped.

    Searching eBay for `iphone 13` and for `iphone 13 128gb` returns different
    price levels, so a capacity stated anywhere in the ad belongs in the query.
    """
    if specs is None:
        return query
    present = set(re.findall(r"[a-z0-9]+", _fold(query)))
    extra = [token for token in specs.query_tokens() if _fold(token) not in present]
    if not extra:
        return query
    budget = int(_id()["sold_query_tokens"]) + 4
    return " ".join([*query.split(), *extra][:budget])


def identity_subject(item: IdentifiedItem) -> str:
    """Text used to match sold comps: the identified product, not a vague title.

    Word overlap for comps is measured on titles so marketplace boilerplate
    does not dilute Jaccard. When the real model only appears in the body,
    the original headline ("Predám telefón") would reject the right comps.
    """
    title = (item.listing.title or "").strip()
    named = (item.canonical_name or "").strip()
    query = (item.search_query or item.model or "").strip()
    if named and named.casefold() != title.casefold():
        return named
    title_tokens = set(significant_tokens(title))
    query_tokens = set(significant_tokens(query))
    if query_tokens and query_tokens - title_tokens:
        return query
    return named or title


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


class ItemSpecs(BaseModel):
    """Price-critical facts about one item, gathered from the whole ad.

    These are the dimensions on which two listings must agree before one may
    price the other. A 64 GB phone is not a 256 GB phone and a lot of eight
    handles is not one handle, however similar the titles read.
    """

    model_config = ConfigDict(frozen=True)

    storage: frozenset[str] = frozenset()
    years: frozenset[str] = frozenset()
    variants: frozenset[str] = frozenset()
    phone: str | None = None
    model_codes: frozenset[str] = frozenset()
    lot_size: int | None = None
    # Mining locality or specimen origin, folded (e.g. "banska stiavnica").
    localities: frozenset[str] = frozenset()

    def is_empty(self) -> bool:
        return not (
            self.storage or self.years or self.variants or self.phone
            or self.model_codes or self.lot_size or self.localities
        )

    def conflicts_with(self, other: ItemSpecs, *, kind: ItemKind | None = None) -> bool:
        """True when `other` may not be used to price `self`.

        Asymmetric on purpose: whatever the candidate states, the comparable has
        to state too. A comparable that mentions extra facts is still usable,
        because most sold listings are described more thoroughly than an ad.
        """
        if self.storage and self.storage != other.storage:
            return True
        if self.years and self.years != other.years:
            return True
        if self.model_codes and not (self.model_codes & other.model_codes):
            return True
        # A multi-piece lot and a single piece are different products.
        if (self.lot_size or 1) != (other.lot_size or 1):
            return True
        if self.localities and not (self.localities & other.localities):
            return True
        if kind is ItemKind.PHONES:
            if self.phone and self.phone != other.phone:
                return True
            if self.variants != other.variants:
                return True
        return False

    def query_tokens(self) -> list[str]:
        """Spec tokens worth appending to a marketplace search."""
        tokens: list[str] = []
        if self.phone:
            tokens.append(self.phone)
        tokens.extend(sorted(self.model_codes))
        tokens.extend(sorted(self.storage))
        tokens.extend(sorted(self.variants))
        tokens.extend(sorted(self.years))
        if self.lot_size and self.lot_size > 1:
            tokens.append(f"{self.lot_size}ks")
        for place in sorted(self.localities):
            tokens.extend(part for part in place.split() if part)
        return tokens


def extract_specs(text: str) -> ItemSpecs:
    return ItemSpecs(
        storage=frozenset(_storage_tokens(text)),
        years=frozenset(_years(text)),
        variants=frozenset(_variant_tokens(text)),
        phone=_phone_signature(text),
        model_codes=frozenset(_model_codes(text)),
        lot_size=_lot_size(text),
        localities=frozenset(_locality_tokens(text)),
    )


# Written-together codes such as EP-OR825 or 1541-II.
_MODEL_CODE_RE = re.compile(r"\b(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*\b")
# Chip families name their part right after the family: "MOS 6510", "CSG 8565".
_PART_FAMILY_RE = re.compile(
    r"\b(?:mos|csg|vic|sid|cia|via|rockwell|commodore|atari|amiga|zilog|intel)\b"
    r"[\s:_-]{0,3}(\d{3,4}[a-z]?\d?)\b"
)
# A revision suffix written apart from the number: "8565 R2".
_REVISION_RE = re.compile(r"\b(\d{3,4})\s*[-_ ]?\s*(r\d)\b")
_LOT_RE = re.compile(r"\b(\d{1,3})\s?(?:ks|kus|kusov|kusy|stk|pcs)\b")
_MODEL_CODE_STOP = {"c64", "c64c", "c64g", "c128", "mp3", "mp4", "usb2", "usb3"}
# A number glued to a unit is a measurement, not a part number.
_UNIT_SUFFIX_RE = re.compile(
    r"^\d+(?:mm|cm|m|g|kg|ml|l|v|w|ah|mah|gb|tb|ks|ct|hz|khz|mhz|k|p|x)$"
)


def _model_codes(text: str) -> set[str]:
    folded = _fold(text)
    storage = _storage_tokens(text)
    codes: set[str] = set()

    for match in _MODEL_CODE_RE.finditer(folded):
        token = match.group(0).replace("-", "")
        if token in storage or token in _MODEL_CODE_STOP or _UNIT_SUFFIX_RE.match(token):
            continue
        # Five characters keeps out 220v and similar ratings.
        if len(token) >= 5 and sum(char.isdigit() for char in token) >= 2:
            codes.add(token)

    codes.update(match.group(1) for match in _PART_FAMILY_RE.finditer(folded))
    codes.update(f"{m.group(1)}{m.group(2)}" for m in _REVISION_RE.finditer(folded))
    return {code for code in codes if code not in _MODEL_CODE_STOP}


def _lot_size(text: str) -> int | None:
    found = [int(size) for size in _LOT_RE.findall(_fold(text))]
    sizes = [size for size in found if 1 < size <= 500]
    return max(sizes) if sizes else None


def _locality_tokens(text: str) -> set[str]:
    """Known mining localities and foreign specimen origins stated in the ad.

    Collectors search species plus locality, so a galenite from Banská Štiavnica
    is not priced from a nameless one. Inflected Slovak ("Banskej Štiavnice")
    still counts. "Slovensko" on a domestic ad does not — that is the seller.
    """
    hay = _fold(text)
    found: set[str] = set()
    for key, names in _place_catalog():
        if _place_mentioned(hay, names):
            found.add(key)
    return found


def _place_catalog() -> list[tuple[str, tuple[str, ...]]]:
    selling = rules().get("selling") or {}
    out: list[tuple[str, tuple[str, ...]]] = []
    for group in ("localities", "countries"):
        entries = selling.get(group) or {}
        for key, names in entries.items():
            folded_key = _fold(str(key))
            if group == "countries" and folded_key in _HOME_ORIGINS:
                continue
            variants = [str(key)]
            if isinstance(names, dict):
                variants.extend(str(value) for value in names.values() if value)
            out.append((folded_key, tuple(dict.fromkeys(variants))))
    return out


def _place_mentioned(hay: str, names: tuple[str, ...]) -> bool:
    for name in names:
        folded = _fold(name)
        if not folded:
            continue
        if re.search(rf"(?<![\w]){re.escape(folded)}(?![\w])", hay):
            return True
        # Banskej Štiavnice / Ľubietovej: match a stem of the distinctive word.
        for word in re.findall(r"[a-z0-9]+", folded):
            if len(word) < 6:
                continue
            stem = word[:-2]
            if len(stem) >= 5 and re.search(rf"(?<![\w]){re.escape(stem)}\w*", hay):
                return True
    return False


def _storage_tokens(text: str) -> set[str]:
    folded = _fold(text)
    out = {f"{size}gb" for size in re.findall(r"\b(16|32|64|128|256|512)\s*(?:gb|g)\b", folded)}
    out.update(f"{size}tb" for size in re.findall(r"\b([1248])\s*tb\b", folded))
    return out


def _years(text: str) -> set[str]:
    # Vintage hardware is priced by production year as much as by model, so the
    # window reaches back past 2000: an 8565R2 from 1991 is not one from 1993.
    return set(re.findall(r"\b(?:19[7-9][0-9]|20(?:[01][0-9]|2[0-9]))\b", _fold(text)))


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


def _hard_specs_match(
    left: str,
    right: str,
    kind: ItemKind,
    left_specs: ItemSpecs | None = None,
) -> bool:
    """Reject comps that differ on price-critical model/spec tokens."""
    # A replacement part must never be priced from complete-product comps, or vice versa.
    if is_replacement_part_text(left) != is_replacement_part_text(right):
        return False
    specs = left_specs if left_specs is not None else extract_specs(left)
    return not specs.conflicts_with(extract_specs(right), kind=kind)


def similar_titles(
    left: str,
    right: str,
    *,
    left_specs: ItemSpecs | None = None,
    left_kind: ItemKind | None = None,
) -> bool:
    """Whether `right` may price `left`.

    Word overlap is measured on titles alone, because marketplace descriptions
    are mostly boilerplate and dilute the similarity. The hard spec gate is the
    opposite: it accepts specs mined from the whole ad through `left_specs`, so
    a capacity that appears only in the body still rejects the wrong comps.
    """
    cfg = _id()
    resolved_left_kind = left_kind if left_kind is not None else classify_kind(left)
    right_kind = classify_kind(right)
    if (
        resolved_left_kind != right_kind
        and resolved_left_kind is not ItemKind.GENERIC
        and right_kind is not ItemKind.GENERIC
    ):
        return False
    kind = resolved_left_kind if resolved_left_kind is not ItemKind.GENERIC else right_kind
    if not _hard_specs_match(left, right, kind, left_specs):
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
