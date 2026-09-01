from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from itertools import zip_longest
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from urllib.parse import urlencode, urljoin

import httpx
from pydantic import BaseModel

from bazar_deals.adapters.central_europe import CentralEuropeClient, SITES, search_url
from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.config import Settings
from bazar_deals.domain import Money, Listing
from datetime import datetime, timezone, timedelta
from bazar_deals.htmlparse import parse_vinted_items
from bazar_deals.rules import rules
from bazar_deals.selling.collect import (
    _clean,
    _fold,
    _price,
    closeness,
    score_match,
    similarity,
    tokens,
)
from bazar_deals.selling.inventory import Inventory, InventoryItem

_AUKRO_SEARCH = "https://backend.aukro.cz/backend-web/api/offers/searchItemsCommon"
_BAZOS_SEARCH = {
    "sk": "https://www.bazos.sk/search.php",
    "cz": "https://www.bazos.cz/search.php",
}
_WANT_PREFIX = re.compile(
    r"(?i)^[^\w]{0,8}((ik|ich|ja|je|j['’]|i)\s*)?"
    r"(kúpim|kupim|koupím|koupim|hľadám|hladam|hledám|hledam|"
    r"suche|kaufe|gesucht|gesuch|szukam|kupię|kupie|kupuję|kupuje|"
    r"poszukuję|poszukuje|keresek|keresem|veszek|megveszem|vennék|venne|"
    r"wanted|wtb|looking\s+for|cherche|achète|achete|"
    r"cerco|compro|comprerei|acquisto|busco|zoek|gezocht|koop|"
    r"cumpăr|cumpar)\b"
)
_SELL_PREFIX = re.compile(
    r"(?i)^[^\w]{0,8}(predám|predam|prodám|prodam|verkaufe|sprzedam|eladó|"
    r"vends|vendo|verkopen|te\s+koop|ofertuję|biete|tausche)\b"
)
_BAZOS_BLOCK_RE = re.compile(
    r'<div class="inzeraty inzeratyflex">.*?'
    r'<h2 class=nadpis><a href="(?P<url>[^"]+)">(?P<title>.*?)</a>'
    r'(?:.*?<div class="inzeratycena"><b><span[^>]*>(?P<price>[^<]*)</span>)?',
    re.S,
)
_KA_ITEM_RE = re.compile(
    r'<article class="aditem"[^>]*data-adid="(?P<id>\d+)"[^>]*data-href="(?P<href>[^"]+)".*?'
    r'<a class="ellipsis"[^>]*>(?P<title>.*?)</a>'
    r'(?:.*?<p class="aditem-main--middle--price-shipping--price">(?P<price>[^<]+))?',
    re.S,
)
_DELCAMPE_SEARCH = "https://www.delcampe.net/en_GB/collectibles/minerals-fossils/search"
_FORUM64_SEARCH = "https://www.forum64.de/index.php?search/"
_DELCAMPE_LINK_RE = re.compile(
    r'<a href="(?P<href>/[^"]+-(?P<id>\d+)\.html)"[^>]*class="item-link"[^>]*>\s*'
    r'<h2 class="item-title[^"]*">(?P<title>.*?)</h2>',
    re.S,
)
_FORUM64_THREAD_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*?thread/(?P<id>\d+)[^"]*)"[^>]*>\s*(?P<title>.*?)\s*</a>',
    re.I | re.S,
)
_MAX_BROAD_PAGES = 2
_MAX_TARGETED = 12
_MATCH_FLOOR = 0.5
_GENERIC_TITLE_WORDS = {
    "pre",
    "pro",
    "na",
    "the",
    "and",
    "und",
    "alle",
    "vsetky",
    "všetky",
    "varianty",
    "procesor",
    "krytal",
    "kristal",
    "leskly",
    "priehladny",
}
# Direct "I will buy" verbs first, then "I'm looking for" on the same boards.
_BAZOS_PHRASES = {
    "sk": ("kúpim", "hľadám", "kaufe", "kupię", "veszek", "compro", "achète", "koop"),
    "cz": ("koupím", "hledám", "kaufe", "kupię", "veszek", "compro", "achète", "koop"),
}
_AUKRO_PHRASES = (
    "koupím",
    "kúpim",
    "kaufe",
    "kupię",
    "veszek",
    "compro",
    "achète",
    "koop",
)
_VINTED_SITES = (
    ("vinted.sk", ("kúpim",)),
    ("vinted.cz", ("koupím",)),
    ("vinted.at", ("kaufe", "suche")),
    ("vinted.de", ("kaufe", "suche")),
    ("vinted.pl", ("kupię", "szukam")),
    ("vinted.hu", ("veszek", "keresek")),
    ("vinted.fr", ("achète", "cherche")),
    ("vinted.it", ("compro", "cerco")),
    ("vinted.nl", ("koop", "zoek")),
    ("vinted.be", ("achète", "koop")),
    ("vinted.es", ("compro", "busco")),
)
_EBAY_BOARDS = (
    ("EBAY_DE", "ebay.de", ("kaufe", "suche")),
    ("EBAY_AT", "ebay.at", ("kaufe", "suche")),
    ("EBAY_FR", "ebay.fr", ("achète", "cherche")),
    ("EBAY_IT", "ebay.it", ("compro", "cerco")),
    ("EBAY_PL", "ebay.pl", ("kupię", "szukam")),
    ("EBAY_NL", "ebay.nl", ("koop", "zoek")),
    ("EBAY_ES", "ebay.es", ("compro", "busco")),
    ("EBAY_BE", "ebay.be", ("achète", "koop")),
)
_KA_PHRASES = ("suche", "kaufe")
_WILLHABEN_PHRASES = ("Suche", "Kaufe")
_DELCAMPE_PHRASES = ("", "suche ", "wanted ")
_FORUM64_PHRASES = ("Suche", "Gesucht")
BUY_VERBS = ("kúpim", "koupím", "kaufe", "kupię", "veszek", "compro", "achète", "koop")
_POWER_NEEDLES = (
    "zdroj",
    "psu",
    "netzteil",
    "napajeci",
    "napajec",
    "napajanie",
    "napajac",
    "trafo",
    "transformer",
    "power supply",
)


class WantAd(BaseModel):
    marketplace: str
    site: str
    external_id: str
    title: str
    url: str
    offer_eur: Decimal | None = None
    query: str = ""


class DemandMatch(BaseModel):
    want: WantAd
    item: InventoryItem
    score: float


@dataclass
class BuyerDigest:
    matches: list[DemandMatch] = field(default_factory=list)
    near_misses: list[DemandMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fetched: Counter[str] = field(default_factory=Counter)
    boards: list[str] = field(default_factory=list)


@dataclass
class _SiteStat:
    queries: int = 0
    rows: int = 0
    wants: int = 0
    blocked: str = ""


def searched_buy_phrases() -> list[str]:
    """All 'I will buy' search words actually sent to classifieds."""
    found: list[str] = []
    seen: set[str] = set()
    for phrases in _BAZOS_PHRASES.values():
        for phrase in phrases:
            key = _fold(phrase)
            if key in seen:
                continue
            seen.add(key)
            found.append(phrase)
    for phrase in _AUKRO_PHRASES:
        key = _fold(phrase)
        if key not in seen:
            seen.add(key)
            found.append(phrase)
    for _host, phrases in _VINTED_SITES:
        for phrase in phrases:
            key = _fold(phrase)
            if key not in seen:
                seen.add(key)
                found.append(phrase)
    for _mid, _host, phrases in _EBAY_BOARDS:
        for phrase in phrases:
            key = _fold(phrase)
            if key not in seen:
                seen.add(key)
                found.append(phrase)
    for phrase in _KA_PHRASES + _WILLHABEN_PHRASES + _FORUM64_PHRASES:
        key = _fold(phrase)
        if key not in seen:
            seen.add(key)
            found.append(phrase)
    return found


def searched_sites() -> list[str]:
    """Classifieds and marketplaces scanned for other people's want-to-buy ads."""
    sites = ["bazos.sk", "bazos.cz", "aukro.cz"]
    sites.extend(host for host, _phrases in _VINTED_SITES)
    sites.append("kleinanzeigen.de")
    sites.append("willhaben.at")
    sites.append("delcampe.net")
    sites.append("forum64.de")
    sites.append("sbazar.cz")
    sites.append("facebook.com")
    sites.extend(host for _mid, host, _wtb in _EBAY_BOARDS)
    return sites


def is_want_to_buy(title: str) -> bool:
    """True when the ad is the buyer's own 'I will buy this' listing."""
    text = (title or "").strip()
    if not text or _SELL_PREFIX.match(text) or _is_song_or_already_bought(text):
        return False
    return bool(_WANT_PREFIX.match(text))


def _is_song_or_already_bought(title: str) -> bool:
    folded = _fold(title)
    if "koupeno" in folded or "koupili jsme" in folded:
        return True
    if "koupim ja si kone" in folded:
        return True
    return False


def queries_for(item: InventoryItem, *, research: bool = False) -> list[str]:
    """Short distinctive queries a European buyer would type."""
    found: list[str] = []
    for part in item.part_numbers:
        token = part.strip()
        if len(token) >= 4:
            found.append(token)
    if item.species:
        head = item.species[0]
        # Species alone first. "kúpim ametyst Brandberg" is too specific and
        # missed real "Kúpim ametyst" ads on the 0-kupec hunts.
        found.append(head)
        place = (item.locality or item.origin).split(",")[-1].strip()
        if place:
            found.append(f"{head} {place}".strip())
        de_name = _german_locality(item)
        if de_name and de_name.casefold() != place.casefold():
            found.append(f"{head} {de_name}".strip())
        if research:
            for spec in item.species[:2]:
                for lang in ("en", "de", "cs"):
                    label = _glossary_name(spec, lang)
                    if label:
                        found.append(label)
    if not found:
        words = [
            word
            for word in re.findall(r"[^\W_]+", item.title, flags=re.UNICODE)
            if len(word) >= 4 and _fold(word) not in _GENERIC_TITLE_WORDS
        ]
        if words:
            found.append(words[0])
            if len(words) > 1:
                found.append(" ".join(words[:3]))
    unique: list[str] = []
    seen: set[str] = set()
    cap = 4 if research else 2
    for query in found:
        key = _fold(query)
        if key in seen:
            continue
        seen.add(key)
        unique.append(query)
        if len(unique) == cap:
            break
    return unique


def _german_locality(item: InventoryItem) -> str:
    blob = _fold(f"{item.locality} {item.origin} {item.title}")
    glossary = ((rules().get("selling") or {}).get("localities") or {})
    if not isinstance(glossary, dict):
        return ""
    for key, names in glossary.items():
        if _fold(str(key)) in blob:
            return str((names or {}).get("de") or "")
    return ""


def _is_power_supply(item: InventoryItem) -> bool:
    blob = _fold(" ".join([item.id, item.title, *item.keywords, *item.part_numbers]))
    return any(needle in blob for needle in _POWER_NEEDLES)


def _title_has_power(title: str) -> bool:
    folded = _fold(title)
    return any(needle in folded for needle in _POWER_NEEDLES)


def match_want(title: str, item: InventoryItem) -> float:
    """Score a want-ad against one inventory item. Part numbers beat fuzzy titles."""
    score = max(score_match(title, item), similarity(title, item.title), closeness(title, item.title))
    folded = _fold(title)
    title_tokens = tokens(title)
    support = _support_tokens(item)
    power_item = _is_power_supply(item)
    if power_item and not _title_has_power(title):
        # A PSU SKU lists 1541/C64 as the machine it fits. A floppy or computer
        # ad with that number is not a buyer for the brick.
        score = min(score, 0.49)
    for part in item.part_numbers:
        token = _fold(part)
        if len(token) < 4 or not _part_in_title(token, title_tokens):
            continue
        if power_item and not _title_has_power(title):
            continue
        if token.isdigit() and not _numeric_part_fits(token, title_tokens, support):
            continue
        score = max(score, 0.82 if token.isdigit() or len(token) >= 5 else 0.7)
    species_hits = []
    for spec in item.species:
        aliases = [spec]
        for lang in ("sk", "cs", "de", "en", "fr", "hu", "pl"):
            label = _glossary_name(spec, lang)
            if label:
                aliases.append(label)
        if any(_fold(alias) in folded for alias in aliases if len(_fold(alias)) >= 4):
            species_hits.append(spec)
    places = []
    if item.locality:
        places.extend(part.strip() for part in item.locality.split(","))
    if item.origin:
        places.append(item.origin)
    german = _german_locality(item)
    if german:
        places.append(german)
    if species_hits:
        score = max(score, 0.62)
    if species_hits and any(_place_in_title(place, folded) for place in places):
        score = max(score, 0.85)
    return score


def _support_tokens(item: InventoryItem) -> set[str]:
    blob = " ".join([*item.keywords, *item.species, *item.part_numbers, item.title])
    return {token for token in tokens(blob) if not token.isdigit() and token not in _GENERIC_TITLE_WORDS}


def _numeric_part_fits(token: str, title_tokens: set[str], support: set[str]) -> bool:
    """'Koupím 6510' is a hit; 'SUCHE John Deere 6510' is a tractor, not a MOS chip."""
    remainder = {word for word in title_tokens if word not in _GENERIC_TITLE_WORDS and not _WANT_PREFIX.match(word)}
    if remainder & support:
        return True
    return remainder <= {token} | support


def _place_in_title(place: str, folded: str) -> bool:
    token = _fold(place)
    if len(token) < 4:
        return False
    if token in folded:
        return True
    stem = token[: max(4, len(token) - 1)]
    return stem in folded


def _part_in_title(token: str, title_tokens: set[str]) -> bool:
    """Whole token only, so postcard A6510 does not match MOS 6510 stock."""
    if token in title_tokens:
        return True
    return f"mos{token}" in title_tokens or f"cbm{token}" in title_tokens


def best_item(title: str, items: list[InventoryItem]) -> tuple[InventoryItem, float] | None:
    ranked = [(match_want(title, item), item) for item in items]
    if not ranked:
        return None
    score, item = max(ranked, key=lambda pair: pair[0])
    if score < _MATCH_FLOOR:
        return None
    return item, score


def find_buyers(
    inventory: Inventory,
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
    manual_listings: list[Listing] | None = None,
    offline: bool = False,
    research: bool = False,
) -> BuyerDigest:
    """Search European want-to-buy ads and pair them with own stock."""
    settings = settings or Settings()
    digest = BuyerDigest()
    items = list(inventory.items)
    queries = _unique_queries(items, research=research)
    ads: dict[str, WantAd] = {}
    stats: dict[str, _SiteStat] = {}
    special_notes: list[str] = []

    def ingest(batch: list[WantAd], note: str, bucket: str) -> None:
        _tally(stats, bucket, batch, note)
        digest.fetched[bucket] += len(batch)
        for ad in batch:
            ads.setdefault(f"{ad.site}:{ad.external_id}", ad)

    for listing in manual_listings or []:
        if listing.raw.get("manual_kind") != "wanted" or not listing.raw.get("available"):
            continue
        checked = datetime.fromisoformat(listing.raw["checked_at"])
        if not timedelta(0) <= datetime.now(timezone.utc) - checked <= timedelta(hours=24):
            special_notes.append(f"{listing.external_id}: stale manual demand; skipped")
            continue
        try:
            price = listing.price.to_eur(settings.eur_czk, eur_pln=settings.eur_pln)
            if listing.price.currency.upper() in {"CZK", "PLN"}:
                price = (price * (1 - settings.fx_fee_rate)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        except ValueError:
            price = None
        ad = WantAd(marketplace=listing.marketplace.value, site=SITES[listing.marketplace.value],
                    external_id=listing.external_id, title=listing.title, url=str(listing.url),
                    offer_eur=price if price and price > 0 else None, query="manual import")
        ingest([ad], f"{ad.site}: user-selected demand (not a confirmed sale)", ad.site)

    if not offline:
        for site, phrases in _BAZOS_PHRASES.items():
            blocked = False
            # Verb+stock first so a 429 does not spend the budget on generic
            # "kúpim" dumps that never match minerals/chips.
            for query in _stock_first_searches(phrases[:2], queries):
                batch, note = _search_bazos(query, site, settings, client=client)
                ingest(batch, note, f"bazos.{site}")
                if _is_hard_block(note):
                    blocked = True
                    break
            if blocked:
                continue
            for phrase in phrases:
                batch, note = _search_bazos(phrase, site, settings, client=client)
                ingest(batch, note, f"bazos.{site}")
                if _is_hard_block(note):
                    break

        if research:
            extra = {
                "sk": ("zakúpim", "wtb"),
                "cz": ("zakoupím", "wtb"),
            }
            for site, phrases in extra.items():
                for phrase in phrases:
                    batch, note = _search_bazos(phrase, site, settings, client=client)
                    ingest(batch, note, f"bazos.{site}")

        aukro_verbs = ("koupím", "kúpim")
        blocked = False
        for query in _stock_first_searches(aukro_verbs, queries):
            batch, note = _search_aukro(query, settings, client=client)
            ingest(batch, note, "aukro")
            if _is_hard_block(note):
                blocked = True
                break
        if not blocked:
            for phrase in _AUKRO_PHRASES:
                batch, note = _search_aukro(phrase, settings, client=client)
                ingest(batch, note, "aukro")
                if _is_hard_block(note):
                    break

        query_hosts = {host for host, _phrases in _VINTED_SITES} if research else {"vinted.sk", "vinted.cz"}
        for site, phrases in _VINTED_SITES:
            blocked = False
            if site in query_hosts and phrases:
                for query in _stock_first_searches((phrases[0],), queries):
                    batch, note = _search_vinted(query, site, settings, client=client)
                    ingest(batch, note, site)
                    if _is_hard_block(note):
                        blocked = True
                        break
            if blocked:
                continue
            for phrase in phrases:
                batch, note = _search_vinted(phrase, site, settings, client=client)
                ingest(batch, note, site)
                if _is_hard_block(note):
                    break

        blocked = False
        for phrase in _KA_PHRASES:
            for query in queries:
                batch, note = _search_kleinanzeigen(query, settings, client=client, wtb=phrase)
                ingest(batch, note, "kleinanzeigen.de")
                if _is_hard_block(note):
                    blocked = True
                    break
            if blocked:
                break

        blocked = False
        for phrase in _WILLHABEN_PHRASES:
            for query in queries:
                batch, note = _search_willhaben(f"{phrase} {query}", settings, client=client)
                ingest(batch, note, "willhaben.at")
                if _is_hard_block(note):
                    blocked = True
                    break
            if blocked:
                break

        blocked = False
        for query in _mineral_search_queries(items):
            for prefix in _DELCAMPE_PHRASES:
                batch, note = _search_delcampe(
                    f"{prefix}{query}".strip(), settings, client=client
                )
                ingest(batch, note, "delcampe.net")
                if _is_hard_block(note):
                    blocked = True
                    break
            if blocked:
                break

        blocked = False
        for query in _retro_search_queries(items):
            for phrase in _FORUM64_PHRASES:
                batch, note = _search_forum64(f"{phrase} {query}", settings, client=client)
                ingest(batch, note, "forum64.de")
                if _is_hard_block(note):
                    blocked = True
                    break
            if blocked:
                break

        if not settings.ebay_client_id or not settings.ebay_client_secret:
            special_notes.append(
                "ebay.de/.at/.fr/.it/.pl/.nl/.es/.be: fetched 0 "
                "(set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)"
            )
        else:
            browse = EbayBrowseClient(settings, client=client)
            try:
                browse._access_token()
            except (RuntimeError, httpx.HTTPError) as exc:
                special_notes.append(
                    f"ebay.de/.at/.fr/.it/.pl/.nl/.es/.be: fetched 0 ({exc})"
                )
            else:
                for marketplace_id, site, phrases in _EBAY_BOARDS:
                    site_count = 0
                    blocked = False
                    for wtb in phrases:
                        for query in queries:
                            batch, note = _search_ebay(
                                f"{wtb} {query}",
                                marketplace_id,
                                site,
                                browse,
                                client=client,
                            )
                            if note:
                                special_notes.append(note)
                                blocked = True
                                break
                            site_count += len(batch)
                            for ad in batch:
                                ads.setdefault(f"{ad.site}:{ad.external_id}", ad)
                        if blocked:
                            break
                    if not blocked:
                        digest.fetched[site] += site_count
                        special_notes.append(f"{site}: fetched {site_count} rows")

        for source, site in SITES.items():
            searcher = CentralEuropeClient(source, settings, client=client)
            if reason := searcher.manual_mode():
                continue
            phrase = "koupím" if source == "sbazar" else "kupię" if source in {"olx", "allegro_pl"} else "kúpim"
            for query in queries[:int(rules()["central_europe"]["max_queries"])]:
                full_query = f"{phrase} {query}"
                try:
                    batch = searcher.search(full_query)
                except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                    if source == "facebook":
                        special_notes.append("facebook: public marketplace unavailable")
                        break
                    special_notes.append(
                        f"{_http_note(site, exc)}; manual search: {search_url(source, full_query)}"
                    )
                    break
                wants = []
                for listing in batch:
                    try:
                        price = listing.price.to_eur(settings.eur_czk, eur_pln=settings.eur_pln)
                        if listing.price.currency.upper() in {"CZK", "PLN"}:
                            price = (price * (1 - settings.fx_fee_rate)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
                        if price <= 0:
                            price = None
                    except ValueError:
                        price = None  # A demand can have an unknown budget.
                    wants.append(WantAd(marketplace=source, site=site, external_id=listing.external_id,
                                        title=listing.title, url=str(listing.url), offer_eur=price, query=full_query))
                ingest(wants, f"{site}: fetched {len(wants)} rows", site)

    matches: list[DemandMatch] = []
    seen_pair: set[str] = set()
    for ad in ads.values():
        hit = best_item(ad.title, items)
        if hit is None:
            continue
        item, score = hit
        key = f"{ad.site}:{ad.external_id}:{item.id}"
        if key in seen_pair:
            continue
        seen_pair.add(key)
        row = DemandMatch(want=ad, item=item, score=score)
        if is_want_to_buy(ad.title):
            matches.append(row)

    matches.sort(key=lambda row: (row.score, row.want.offer_eur or Decimal("0")), reverse=True)
    digest.matches = matches
    digest.near_misses = []
    digest.boards = list(stats.keys())
    digest.notes = [_format_site_stat(site, stat) for site, stat in stats.items()] + special_notes
    return digest


def format_buyer_digest(digest: BuyerDigest, *, mention: str = "") -> str:
    ping = f"@{mention}\n\n" if mention and digest.matches else ""
    notes = "\n".join(f"- {note}" for note in digest.notes) or "- (no sources fetched)"
    skipped = _format_near_misses(digest.near_misses)
    if not digest.matches:
        boards = ", ".join(digest.boards) if digest.boards else "(žiadne)"
        return (
            f"{ping}**0 kupcov** na tvoj tovar. Digest je prázdny, kým sa nenájde "
            f"inzerát typu kúpim/koupím/kaufe/kupię/veszek/compro/achète/koop "
            f"spárovaný so skladom.\n\n"
            f"Servery: {boards}\n\n"
            f"{skipped}"
            f"Zdroje:\n{notes}\n"
        )
    markers = "\n".join(
        f"<!-- want:{row.want.site}:{row.want.external_id}:{row.item.id} -->"
        for row in digest.matches
    )
    blocks = "\n\n---\n\n".join(_format_match(row) for row in digest.matches)
    return (
        f"{ping}{markers}\n"
        f"**{len(digest.matches)} kupec/kupci** na tvoj tovar\n\n"
        f"{blocks}\n\n"
        f"{skipped}"
        f"Zdroje:\n{notes}\n"
    )


_NEAR_MISS_CAP = 20


def _format_near_misses(rows: list[DemandMatch]) -> str:
    if not rows:
        return ""
    lines = []
    for row in rows[:_NEAR_MISS_CAP]:
        want = row.want
        lines.append(
            f"- [{want.title}]({want.url}) · {want.site} · sklad `{row.item.id}`"
        )
    extra = f"\n- … a ešte {len(rows) - _NEAR_MISS_CAP}" if len(rows) > _NEAR_MISS_CAP else ""
    return (
        f"**{len(rows)} inzerát(ov) sedí na sklad, ale názov nie je dopyt kúpim** "
        f"(väčšinou predaj; over link):\n"
        + "\n".join(lines)
        + extra
        + "\n\n"
    )


def _format_match(row: DemandMatch) -> str:
    want = row.want
    item = row.item
    offer = f"{want.offer_eur} €" if want.offer_eur not in (None, Decimal("0")) else "neuvedené"
    listed = ", ".join(f"{market} {price} €" for market, price in sorted(item.listed.items())) or "nikde neuvedené"
    return (
        f"### {item.title}\n"
        f"- **identifikácia:** `{item.id}` · {item.segment}\n"
        f"- **kde kupec je:** [{want.site}]({want.url})\n"
        f"- **dopyt (názov inzerátu):** {want.title}\n"
        f"- **chce kúpiť za:** {offer}\n"
        f"- **tvoje inzeráty:** {listed}\n"
        f"- zhoda: {row.score:.2f}"
    )


def _stock_first_searches(phrases: tuple[str, ...] | list[str], queries: list[str]) -> list[str]:
    """`kúpim ametyst` before a generic `kúpim` dump that 429s the board."""
    found: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for phrase in phrases:
            text = f"{phrase} {query}".strip()
            key = _fold(text)
            if not text or key in seen:
                continue
            seen.add(key)
            found.append(text)
    return found


def _unique_queries(items: list[InventoryItem], *, research: bool = False) -> list[str]:
    # Give every stock item its primary query before spending requests on
    # alternate names. The old global cap let early minerals exclude all chips.
    found: list[str] = []
    seen: set[str] = set()
    segments: dict[str, list[InventoryItem]] = defaultdict(list)
    for item in items:
        segments[item.segment].append(item)
    ordered = [item for row in zip_longest(*segments.values()) for item in row if item is not None]
    groups = [queries_for(item, research=research) for item in ordered]
    budget = max(_MAX_TARGETED * (2 if research else 1), sum(bool(group) for group in groups))
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            query = group[index]
            key = _fold(query)
            if key in seen:
                continue
            seen.add(key)
            found.append(query)
            if len(found) >= budget:
                return found
    return found


def _mineral_search_queries(items: list[InventoryItem]) -> list[str]:
    """English/German collector names Delcampe listings actually use."""
    found: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        token = query.strip()
        key = _fold(token)
        if len(token) < 4 or key in seen:
            return
        seen.add(key)
        found.append(token)

    for item in items:
        if item.segment != "minerals":
            continue
        place = ""
        if item.locality:
            place = item.locality.split(",")[-1].strip()
        elif item.origin:
            place = item.origin.strip()
        german_place = _german_locality(item)
        head = item.species[0] if item.species else ""
        english = _glossary_name(head, "en")
        german = _glossary_name(head, "de")
        if english and german_place:
            add(f"{english} {german_place}")
        if english and place and _fold(place) != _fold(german_place):
            add(f"{english} {place}")
        if german and german_place:
            add(f"{german} {german_place}")
        if not english and not german:
            for query in queries_for(item):
                add(query)
        if len(found) >= _MAX_TARGETED:
            break
    return found[:_MAX_TARGETED]


def _retro_search_queries(items: list[InventoryItem]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.segment != "retro":
            continue
        for query in queries_for(item):
            key = _fold(query)
            if key in seen:
                continue
            seen.add(key)
            found.append(query)
            if len(found) >= _MAX_TARGETED:
                return found
    return found


def _glossary_name(word: str, lang: str) -> str:
    if not word:
        return ""
    glossary = (rules().get("selling") or {}).get("glossary") or {}
    if not isinstance(glossary, dict):
        return ""
    names = glossary.get(word)
    if not isinstance(names, dict):
        for key, value in glossary.items():
            if _fold(str(key)) == _fold(word) and isinstance(value, dict):
                names = value
                break
        else:
            return ""
    return str(names.get(lang) or "").strip()


def _search_bazos(
    query: str,
    site: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    url = _BAZOS_SEARCH[site]
    ads: list[WantAd] = []
    for page in range(_MAX_BROAD_PAGES):
        params = {"hledat": query, "rubriky": "www", "hlokalita": "", "humkreis": "25"}
        if page:
            params["crz"] = str(page * 20)
        try:
            response = _get(url, settings, client=client, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return ads, _http_note(f"bazos.{site}", exc)
        for match in _BAZOS_BLOCK_RE.finditer(response.text):
            href = match.group("url")
            if href.startswith("/"):
                href = urljoin(url, href)
            title = _clean(match.group("title"))
            identifier = href.rsplit("/inzerat/", 1)[-1].split("/")[0]
            raw_price = match.group("price") or ""
            amount = _price(raw_price) if raw_price.strip() else Decimal("0")
            if amount and site == "cz":
                amount = (amount / settings.eur_czk).quantize(Decimal("0.01")) if settings.eur_czk else None
            ads.append(
                WantAd(
                    marketplace="bazos",
                    site=f"bazos.{site}",
                    external_id=identifier,
                    title=title,
                    url=href,
                    offer_eur=amount or None,
                    query=query,
                )
            )
        _pause(settings, client)
    return ads, f"bazos.{site}: fetched {len(ads)} want-ads for {query!r}"


def _search_aukro(
    query: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    ads: list[WantAd] = []
    for page in range(_MAX_BROAD_PAGES):
        try:
            response = _post(
                _AUKRO_SEARCH,
                settings,
                client=client,
                params={"page": page, "size": 40},
                json={"text": query, "fallbackItemsCount": 4},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ads, _http_note("aukro", exc)
        for node in payload.get("content") or []:
            if node.get("adultContent"):
                continue
            title = str(node.get("itemName") or "").strip()
            identifier = str(node.get("itemId") or "")
            seo = str(node.get("seoUrl") or "").strip()
            if not identifier or not title:
                continue
            price = node.get("buyNowPrice") if isinstance(node.get("buyNowPrice"), dict) else {}
            amount = Decimal(str(price.get("amount") or "0"))
            currency = str(price.get("currency") or "CZK")
            try:
                amount = Money(amount=amount, currency=currency).to_eur(settings.eur_czk, eur_pln=settings.eur_pln)
            except ValueError:
                amount = None
            ads.append(
                WantAd(
                    marketplace="aukro",
                    site="aukro.cz",
                    external_id=identifier,
                    title=title,
                    url=f"https://aukro.sk/{seo}-{identifier}" if seo else f"https://aukro.cz/{identifier}",
                    offer_eur=amount or None,
                    query=query,
                )
            )
        _pause(settings, client)
    return ads, f"aukro: fetched {len(ads)} rows for {query!r}"


def _search_vinted(
    query: str,
    site: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    url = f"https://www.{site}/catalog?" + urlencode(
        {"search_text": query, "order": "newest_first", "page": 1}
    )
    try:
        response = _get(url, settings, client=client)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], _http_note(site, exc)
    ads = []
    for listing in parse_vinted_items(response.text):
        href = str(listing.url)
        if "vinted.sk" in href and site != "vinted.sk":
            href = href.replace("www.vinted.sk", f"www.{site}", 1)
        ads.append(
            WantAd(
                marketplace="vinted",
                site=site,
                external_id=listing.external_id,
                title=listing.title,
                url=href,
                offer_eur=listing.price.amount or None,
                query=query,
            )
        )
    _pause(settings, client)
    return ads, f"{site}: fetched {len(ads)} rows for {query!r}"


def _search_kleinanzeigen(
    query: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
    wtb: str = "suche",
) -> tuple[list[WantAd], str]:
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(f"{wtb} {query}")).strip("-")
    url = f"https://www.kleinanzeigen.de/s-{slug}/k0"
    try:
        response = _get(url, settings, client=client)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], _http_note("kleinanzeigen.de", exc)
    ads: list[WantAd] = []
    for match in _KA_ITEM_RE.finditer(response.text):
        href = match.group("href")
        if href.startswith("/"):
            href = "https://www.kleinanzeigen.de" + href
        raw_price = match.group("price") or ""
        amount = _price(raw_price) if raw_price.strip() else Decimal("0")
        ads.append(
            WantAd(
                marketplace="kleinanzeigen",
                site="kleinanzeigen.de",
                external_id=match.group("id"),
                title=_clean(match.group("title")),
                url=href,
                offer_eur=amount or None,
                query=query,
            )
        )
    _pause(settings, client)
    want_n = sum(1 for ad in ads if is_want_to_buy(ad.title))
    return ads, (
        f"kleinanzeigen.de: fetched {len(ads)} rows ({want_n} want-ads) "
        f"for {wtb!r} {query!r}"
    )


def _search_willhaben(
    query: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    url = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?" + urlencode(
        {"keyword": query}
    )
    try:
        response = _get(url, settings, client=client)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], _http_note("willhaben.at", exc)
    ads: list[WantAd] = []
    blob = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text,
        re.S,
    )
    if not blob:
        _pause(settings, client)
        return [], "willhaben.at: fetched 0 (no listings payload)"
    try:
        payload = json.loads(blob.group(1))
        rows = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("searchResult", {})
            .get("advertSummaryList", {})
            .get("advertSummary")
            or []
        )
    except ValueError as exc:
        return [], _http_note("willhaben.at", exc)
    if isinstance(rows, dict):
        rows = [rows]
    for node in rows:
        attrs = {
            item.get("name"): (item.get("values") or [""])[0]
            for item in ((node.get("attributes") or {}).get("attribute") or [])
            if isinstance(item, dict)
        }
        title = str(attrs.get("HEADING") or node.get("description") or "").strip()
        identifier = str(node.get("id") or attrs.get("ADID") or "")
        seo = str(attrs.get("SEO_URL") or "").strip()
        if not title or not identifier:
            continue
        try:
            amount = Decimal(str(attrs.get("PRICE") or "0"))
        except InvalidOperation:
            amount = Decimal("0")
        href = f"https://www.willhaben.at/iad/{seo}" if seo else (
            f"https://www.willhaben.at/iad/object?adId={identifier}"
        )
        ads.append(
            WantAd(
                marketplace="willhaben",
                site="willhaben.at",
                external_id=identifier,
                title=title,
                url=href,
                offer_eur=amount or None,
                query=query,
            )
        )
    _pause(settings, client)
    want_n = sum(1 for ad in ads if is_want_to_buy(ad.title))
    return ads, (
        f"willhaben.at: fetched {len(ads)} rows ({want_n} want-ads) for {query!r}"
    )


def _search_delcampe(
    query: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    url = _DELCAMPE_SEARCH + "?" + urlencode({"term": query})
    try:
        response = _get(
            url,
            settings,
            client=client,
            extra_headers={"Accept-Language": "en-GB,en;q=0.9,de;q=0.8"},
        )
        if _cloudflare_blocked(response):
            return [], "delcampe.net: fetched 0 (Cloudflare blocked datacenter requests)"
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], _http_note("delcampe.net", exc)
    ads: list[WantAd] = []
    for match in _DELCAMPE_LINK_RE.finditer(response.text):
        href = match.group("href")
        if href.startswith("/"):
            href = "https://www.delcampe.net" + href
        title = _clean(match.group("title"))
        identifier = match.group("id")
        if not title or not identifier:
            continue
        tail = response.text[match.end() : match.end() + 500]
        price_m = re.search(r'class="item-price[^"]*">(?P<p>[^<]+)', tail)
        amount = None
        if price_m:
            raw = _clean(price_m.group("p"))
            if "€" in raw or "EUR" in raw.upper():
                parsed = _price(raw)
                amount = parsed or None
        ads.append(
            WantAd(
                marketplace="delcampe",
                site="delcampe.net",
                external_id=identifier,
                title=title,
                url=href,
                offer_eur=amount,
                query=query,
            )
        )
    _pause(settings, client)
    want_n = sum(1 for ad in ads if is_want_to_buy(ad.title))
    return ads, (
        f"delcampe.net: fetched {len(ads)} rows ({want_n} want-ads) for {query!r}"
    )


def _search_forum64(
    query: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    try:
        response = _get(
            _FORUM64_SEARCH,
            settings,
            client=client,
            params={"q": query, "sortOrder": "DESC"},
            extra_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
        )
        if _cloudflare_blocked(response):
            return [], "forum64.de: fetched 0 (Cloudflare blocked datacenter requests)"
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], _http_note("forum64.de", exc)
    ads: list[WantAd] = []
    seen: set[str] = set()
    for match in _FORUM64_THREAD_RE.finditer(response.text):
        title = _clean(match.group("title"))
        identifier = match.group("id")
        if not identifier or identifier in seen or len(title) < 8:
            continue
        seen.add(identifier)
        href = match.group("href").replace("&amp;", "&")
        if href.startswith("/"):
            href = "https://www.forum64.de" + href
        elif not href.startswith("http"):
            href = urljoin(_FORUM64_SEARCH, href)
        ads.append(
            WantAd(
                marketplace="forum64",
                site="forum64.de",
                external_id=identifier,
                title=title,
                url=href,
                offer_eur=None,
                query=query,
            )
        )
    _pause(settings, client)
    want_n = sum(1 for ad in ads if is_want_to_buy(ad.title))
    return ads, (
        f"forum64.de: fetched {len(ads)} rows ({want_n} want-ads) for {query!r}"
    )


def _cloudflare_blocked(response: httpx.Response) -> bool:
    snippet = (response.text or "")[:4000]
    if "Just a moment" not in snippet and "challenge-platform" not in snippet:
        return False
    return (
        response.status_code in {403, 503}
        or "Cloudflare" in snippet
        or "challenge-platform" in snippet
        or "cf-browser-verification" in snippet
    )


def _tally(stats: dict[str, _SiteStat], site: str, batch: list[WantAd], note: str) -> None:
    stat = stats.setdefault(site, _SiteStat())
    stat.queries += 1
    stat.rows += len(batch)
    stat.wants += sum(1 for ad in batch if is_want_to_buy(ad.title))
    if not stat.blocked and _is_hard_block(note):
        stat.blocked = _block_reason(note)


def _format_site_stat(site: str, stat: _SiteStat) -> str:
    parts = [f"{site}: {stat.queries} queries", f"{stat.rows} rows", f"{stat.wants} want-ads"]
    if stat.blocked:
        parts.append(f"stopped after {stat.blocked}")
    return " · ".join(parts)


def _http_note(site: str, exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) if response is not None else None
    if status:
        return f"{site}: HTTP {status}"
    text = " ".join(str(exc).split())
    if len(text) > 80:
        text = text[:77] + "..."
    return f"{site}: {text or 'request failed'}"


def _block_reason(note: str) -> str:
    if "Cloudflare" in note:
        return "Cloudflare"
    match = re.search(r"HTTP (\d{3})", note)
    if match:
        return f"HTTP {match.group(1)}"
    if "403" in note or "Forbidden" in note:
        return "HTTP 403"
    if "429" in note:
        return "HTTP 429"
    return "blocked"


def _is_hard_block(note: str) -> bool:
    folded = (note or "").casefold()
    return (
        "cloudflare" in folded
        or "http 403" in folded
        or "http 429" in folded
        or "http 503" in folded
        or " 403" in note
        or "forbidden" in folded
    )


def _search_ebay(
    query: str,
    marketplace_id: str,
    site: str,
    browse: EbayBrowseClient,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    try:
        headers = {
            "Authorization": f"Bearer {browse._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        }
        params = {"q": query, "sort": "newlyListed", "limit": "40"}
        if client is not None:
            response = client.get(rules()["ebay"]["search_url"], headers=headers, params=params)
        else:
            response = httpx.get(
                rules()["ebay"]["search_url"], headers=headers, params=params, timeout=20.0
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return [], _http_note(site, exc)
    ads: list[WantAd] = []
    for node in payload.get("itemSummaries") or []:
        title = str(node.get("title") or "").strip()
        identifier = str(node.get("itemId") or "")
        href = str(node.get("itemWebUrl") or "")
        if not title or not identifier:
            continue
        price = node.get("price") or {}
        try:
            amount = Decimal(str(price.get("value") or "0"))
        except InvalidOperation:
            amount = Decimal("0")
        ads.append(
            WantAd(
                marketplace="ebay",
                site=site,
                external_id=identifier,
                title=title,
                url=href,
                offer_eur=amount or None,
                query=query,
            )
        )
    return ads, ""


def _pause(settings: Settings, client: httpx.Client | None) -> None:
    if client is not None:
        return
    time.sleep(min(0.4, settings.bazos_request_gap_seconds))


def _get(
    url: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
    params=None,
    extra_headers: dict[str, str] | None = None,
):
    headers = {"User-Agent": settings.bazos_user_agent, "Accept": "text/html"}
    if extra_headers:
        headers.update(extra_headers)
    if client is not None:
        return client.get(url, headers=headers, params=params)
    return httpx.get(url, headers=headers, params=params, timeout=30.0, follow_redirects=True)


def _post(url: str, settings: Settings, *, client: httpx.Client | None, params=None, json=None):
    headers = {
        "User-Agent": settings.bazos_user_agent,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if client is not None:
        return client.post(url, headers=headers, params=params, json=json)
    return httpx.post(
        url, headers=headers, params=params, json=json, timeout=30.0, follow_redirects=True
    )
