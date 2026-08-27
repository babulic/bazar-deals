from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urljoin

import httpx
from pydantic import BaseModel

from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.config import Settings
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
    r"(?i)^[^\w]{0,8}(kúpim|kupim|koupím|koupim|hľadám|hladam|hledám|hledam|"
    r"suche|kaufe|szukam|keresek|wanted|wtb)\b"
)
_SELL_PREFIX = re.compile(
    r"(?i)^[^\w]{0,8}(predám|predam|prodám|prodam|verkaufe|sprzedam|eladó)\b"
)
_BAZOS_BLOCK_RE = re.compile(
    r'<div class="inzeraty inzeratyflex">.*?'
    r'<h2 class=nadpis><a href="(?P<url>[^"]+)">(?P<title>.*?)</a>'
    r'(?:.*?<div class="inzeratycena"><b><span[^>]*>(?P<price>[^<]*)</span>)?',
    re.S,
)
_MAX_BROAD_PAGES = 2
_MAX_TARGETED = 24
_MATCH_FLOOR = 0.5
_VINTED_SITES = (
    ("vinted.sk", "kúpim"),
    ("vinted.cz", "koupím"),
    ("vinted.at", "suche"),
    ("vinted.de", "suche"),
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
    notes: list[str] = field(default_factory=list)
    fetched: Counter[str] = field(default_factory=Counter)


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


def queries_for(item: InventoryItem) -> list[str]:
    """Short distinctive queries a European buyer would type."""
    found: list[str] = []
    for part in item.part_numbers:
        token = part.strip()
        if len(token) >= 4:
            found.append(token)
    if item.species:
        place = (item.locality or item.origin).split(",")[0].strip()
        head = item.species[0]
        found.append(f"{head} {place}".strip() if place else head)
    if not found:
        words = [word for word in re.findall(r"[A-Za-z0-9\-]+", item.title) if len(word) >= 4]
        if words:
            found.append(" ".join(words[:3]))
    unique: list[str] = []
    seen: set[str] = set()
    for query in found:
        key = _fold(query)
        if key in seen:
            continue
        seen.add(key)
        unique.append(query)
        if len(unique) == 2:
            break
    return unique


def match_want(title: str, item: InventoryItem) -> float:
    """Score a want-ad against one inventory item. Part numbers beat fuzzy titles."""
    score = max(score_match(title, item), similarity(title, item.title), closeness(title, item.title))
    folded = _fold(title)
    title_tokens = tokens(title)
    for part in item.part_numbers:
        token = _fold(part)
        if len(token) >= 4 and _part_in_title(token, title_tokens):
            score = max(score, 0.82 if token.isdigit() or len(token) >= 5 else 0.7)
    species_hits = [spec for spec in item.species if _fold(spec) in folded]
    places = []
    if item.locality:
        places.extend(part.strip() for part in item.locality.split(","))
    if item.origin:
        places.append(item.origin)
    if species_hits and any(_place_in_title(place, folded) for place in places):
        score = max(score, 0.85)
    return score


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
) -> BuyerDigest:
    """Search European want-to-buy ads and pair them with own stock."""
    settings = settings or Settings()
    digest = BuyerDigest()
    items = list(inventory.items)
    queries = _unique_queries(items)
    ads: dict[str, WantAd] = {}

    for site, phrase in (("sk", "kúpim"), ("cz", "koupím")):
        batch, note = _search_bazos(phrase, site, settings, client=client)
        digest.notes.append(note)
        digest.fetched[f"bazos.{site}"] += len(batch)
        for ad in batch:
            ads.setdefault(f"{ad.site}:{ad.external_id}", ad)

    for phrase in ("koupím", "kúpim"):
        batch, note = _search_aukro(phrase, settings, client=client)
        digest.notes.append(note)
        digest.fetched["aukro"] += len(batch)
        for ad in batch:
            ads.setdefault(f"{ad.site}:{ad.external_id}", ad)

    for site, phrase in _VINTED_SITES:
        batch, note = _search_vinted(phrase, site, settings, client=client)
        digest.notes.append(note)
        digest.fetched[site] += len(batch)
        for ad in batch:
            ads.setdefault(f"{ad.site}:{ad.external_id}", ad)

    for marketplace_id, site in (("EBAY_DE", "ebay.de"), ("EBAY_AT", "ebay.at")):
        site_count = 0
        blocked = False
        for query in queries:
            batch, note = _search_ebay(f"suche {query}", marketplace_id, site, settings, client=client)
            if note:
                digest.notes.append(note)
                blocked = True
                break
            site_count += len(batch)
            for ad in batch:
                ads.setdefault(f"{ad.site}:{ad.external_id}", ad)
        if not blocked:
            digest.fetched[site] += site_count
            digest.notes.append(f"{site}: fetched {site_count} rows")

    matches: list[DemandMatch] = []
    seen_pair: set[str] = set()
    for ad in ads.values():
        if not is_want_to_buy(ad.title):
            continue
        hit = best_item(ad.title, items)
        if hit is None:
            continue
        item, score = hit
        key = f"{ad.site}:{ad.external_id}:{item.id}"
        if key in seen_pair:
            continue
        seen_pair.add(key)
        matches.append(DemandMatch(want=ad, item=item, score=score))

    matches.sort(key=lambda row: (row.score, row.want.offer_eur or Decimal("0")), reverse=True)
    digest.matches = matches
    return digest


def format_buyer_digest(digest: BuyerDigest, *, mention: str = "") -> str:
    ping = f"@{mention}\n\n" if mention and digest.matches else ""
    if not digest.matches:
        notes = "\n".join(f"- {note}" for note in digest.notes) or "- (no sources fetched)"
        return (
            f"{ping}**0 kupcov** na tvoj tovar. Digest je prázdny, kým sa nenájde "
            f"inzerát typu kúpim/suche spárovaný so skladom.\n\nZdroje:\n{notes}\n"
        )
    markers = "\n".join(
        f"<!-- want:{row.want.site}:{row.want.external_id}:{row.item.id} -->"
        for row in digest.matches
    )
    blocks = "\n\n---\n\n".join(_format_match(row) for row in digest.matches)
    notes = "\n".join(f"- {note}" for note in digest.notes)
    return (
        f"{ping}{markers}\n"
        f"**{len(digest.matches)} kupec/kupci** na tvoj tovar\n\n"
        f"{blocks}\n\n"
        f"Zdroje:\n{notes}\n"
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


def _unique_queries(items: list[InventoryItem]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for item in items:
        for query in queries_for(item):
            key = _fold(query)
            if key in seen:
                continue
            seen.add(key)
            found.append(query)
            if len(found) >= _MAX_TARGETED:
                return found
    return found


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
            return ads, f"bazos.{site}: fetched {len(ads)} ({exc})"
        for match in _BAZOS_BLOCK_RE.finditer(response.text):
            href = match.group("url")
            if href.startswith("/"):
                href = urljoin(url, href)
            title = _clean(match.group("title"))
            identifier = href.rsplit("/inzerat/", 1)[-1].split("/")[0]
            raw_price = match.group("price") or ""
            amount = _price(raw_price) if raw_price.strip() else Decimal("0")
            if amount and site == "cz":
                amount = (amount / settings.eur_czk).quantize(Decimal("0.01"))
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
    eur_czk = Decimal(str(rules()["selling"]["aukro_eur_czk"]))
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
            return ads, f"aukro: fetched {len(ads)} ({exc})"
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
            if amount and currency.upper() == "CZK" and eur_czk:
                amount = (amount / eur_czk).quantize(Decimal("0.01"))
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
        return [], f"{site}: fetched 0 ({exc})"
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


def _search_ebay(
    query: str,
    marketplace_id: str,
    site: str,
    settings: Settings,
    *,
    client: httpx.Client | None,
) -> tuple[list[WantAd], str]:
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        return [], f"{site}: fetched 0 (set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)"
    local = settings.model_copy(update={"ebay_marketplace": marketplace_id})
    browse = EbayBrowseClient(local)
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
        return [], f"{site}: fetched 0 ({exc})"
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


def _get(url: str, settings: Settings, *, client: httpx.Client | None, params=None):
    headers = {"User-Agent": settings.bazos_user_agent, "Accept": "text/html"}
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
