from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from pydantic import BaseModel

from bazar_deals.rules import rules

# Sizes and weights are the first thing a mineral collector filters on, and they
# survive translation untouched, so they are worth pulling out of the prose.
_MEASUREMENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mm|cm|m|g|kg|ct)\b", re.IGNORECASE)

# Hardware model tokens that buyers type verbatim regardless of their language.
_COMPAT_RE = re.compile(
    r"\b(?:C64C|C64G|C64|C128|VIC-II|VIA|1541 II|1541|1571|1581|1570|"
    r"Commodore|Atari|Amstrad|Spectrum|Amiga)\b",
    re.IGNORECASE,
)

# Detection keyword in the Slovak title -> glossary entry for the device noun.
_DEVICE_TERMS = {
    "videochip": "videočip",
    "videočip": "videočip",
    "zdroj": "zdroj",
    "procesor": "procesor",
    "disketov": "disketová jednotka",
    "joystick": "joystick",
}


class TitlePart(BaseModel):
    """One title fragment plus how hard the fitter should try to keep it.

    Priority 1 is never dropped voluntarily; higher numbers go first when the
    channel's character budget runs out.
    """

    text: str
    priority: int = 5


@lru_cache(maxsize=1)
def _selling() -> dict:
    return rules()["selling"]


def fold(text: str) -> str:
    """Accent- and case-insensitive key, so 'Banska' matches 'Banská'."""
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(char for char in stripped if not unicodedata.combining(char)).lower()


def truncate_on_word_boundary(text: str, limit: int) -> str:
    """Cut to `limit` characters without leaving a half word behind."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = window.rfind(" ")
    # A single word longer than the whole budget has to be cut mid-word.
    trimmed = window[:cut] if cut > 0 else window
    return trimmed.rstrip(" ,;:-+/")


def fit_parts(parts: list[TitlePart], limit: int, *, separator: str = ", ") -> str:
    """Join parts in order, dropping the least important ones until they fit."""
    kept = [part for part in parts if part.text.strip()]
    while kept:
        candidate = separator.join(part.text for part in kept)
        if len(candidate) <= limit:
            return candidate
        droppable = [part for part in kept if part.priority > 1]
        if not droppable:
            break
        # Ties break towards the later fragment, keeping the leading keywords.
        worst = max(droppable, key=lambda part: (part.priority, kept.index(part)))
        kept.remove(worst)
    return truncate_on_word_boundary(separator.join(part.text for part in kept), limit)


def localize(key: str, language: str, *, default: str | None = None) -> str:
    entry = _selling()["glossary"].get(key.lower())
    if entry is None:
        return default if default is not None else key
    return entry.get(language) or entry.get("en") or key


def localize_locality(key: str, language: str) -> tuple[str, str]:
    """Return (name in `language`, modern Slovak name) for a known locality.

    German and Hungarian collectors search the historic mining names, so the
    exonym leads and the modern name follows only when the budget allows.
    """
    entry = _selling()["localities"].get(key.lower())
    if entry is None:
        return key, key
    return entry.get(language) or entry.get("en") or key, entry.get("sk") or key


def localize_origin(key: str, language: str) -> str:
    entry = _selling()["countries"].get(key.lower())
    if entry is None:
        return key
    return entry.get(language) or entry.get("en") or key


def measurements(text: str) -> list[str]:
    return [match.group(0).replace(" ", "") for match in _MEASUREMENT_RE.finditer(text)]


def compatibility(text: str) -> list[str]:
    seen: list[str] = []
    for match in _COMPAT_RE.finditer(text):
        token = match.group(0)
        if token.lower() not in {item.lower() for item in seen}:
            seen.append(token)
    return seen


def device_noun(text: str, language: str) -> str:
    lowered = text.lower()
    for needle, glossary_key in _DEVICE_TERMS.items():
        if needle in lowered:
            return localize(glossary_key, language)
    return ""


def mineral_parts(item, language: str) -> list[TitlePart]:
    parts: list[TitlePart] = []
    species = list(item.species)

    # Species, specimen form and size read as one noun phrase, the way collector
    # listings are actually written: "Amethyst Kristall 74mm".
    head = [localize(species[0], language)] if species else []
    if item.form:
        head.append(localize(item.form, language))
    sizes = measurements(item.title)
    if sizes:
        head.append(sizes[0])
    if head:
        parts.append(TitlePart(text=" ".join(head), priority=1))

    for extra in species[1:3]:
        parts.append(TitlePart(text=localize(extra, language), priority=6))

    if item.locality:
        local_name, slovak_name = localize_locality(item.locality, language)
        parts.append(TitlePart(text=local_name, priority=2))
        # Only worth repeating when the exonym is a genuinely different word;
        # a mere transliteration would waste characters.
        if slovak_name and fold(slovak_name) != fold(local_name):
            parts.append(TitlePart(text=slovak_name, priority=7))

    if item.origin:
        origin = localize_origin(item.origin, language)
        if fold(origin) not in {fold(part.text) for part in parts}:
            parts.append(TitlePart(text=origin, priority=4))
    return parts


def retro_parts(item, language: str) -> list[TitlePart]:
    parts: list[TitlePart] = []
    numbers = list(item.part_numbers)
    if not numbers:
        return parts

    # The part number carries the whole search query in this niche, so it leads
    # together with the chip family label and the device noun.
    labels = [number for number in numbers[1:] if not number[0].isdigit()]
    head = [numbers[0], *labels[:1]]
    noun = device_noun(item.title, language)
    if noun:
        head.append(noun)
    parts.append(TitlePart(text=" ".join(head), priority=1))

    covered = {fold(token) for token in head}
    compat = [token for token in compatibility(item.title) if fold(token) not in covered]
    if compat:
        parts.append(TitlePart(text=" ".join(compat[:2]), priority=2))
        covered.update(fold(token) for token in compat[:2])

    for extra in numbers[1:]:
        if fold(extra) not in covered:
            parts.append(TitlePart(text=extra, priority=6))
    return parts


def build_title(item, *, language: str, limit: int) -> str:
    """Compose a keyword-correct title that fits the channel's character budget.

    This is not a translation of the Slovak prose. It rebuilds the title from the
    structured fields so the terms buyers actually type -- species, historic
    locality names, part numbers -- are present in the target language, then
    trims the least valuable fragment until the title fits.
    """
    if item.segment == "minerals":
        parts = mineral_parts(item, language)
    elif item.segment == "retro":
        parts = retro_parts(item, language)
    else:
        parts = []
    if not parts:
        return truncate_on_word_boundary(item.title, limit)

    covered = {fold(part.text) for part in parts}
    segment = _selling()["segments"].get(item.segment) or {}
    for keyword in [*item.keywords, *(segment.get("filler_keywords") or [])]:
        text = localize(keyword, language)
        if fold(text) not in covered:
            covered.add(fold(text))
            parts.append(TitlePart(text=text, priority=8))
    return fit_parts(parts, limit)
