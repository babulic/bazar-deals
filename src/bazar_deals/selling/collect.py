from __future__ import annotations

import difflib
import html
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import BaseModel, Field

from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.adapters.vinted import VintedProClient
from bazar_deals.config import Settings
from bazar_deals.domain import Money
from bazar_deals.rules import rules
from bazar_deals.selling.inventory import Inventory, InventoryItem

_AUKRO_SEARCH = "https://backend.aukro.cz/backend-web/api/offers/searchItemsCommon"
_BAZOS_SEARCH = "https://www.bazos.sk/search.php"
_EBAY_SEARCH = rules()["ebay"]["search_url"]

# bazos.sk paginates its search with a fixed offset step.
BAZOS_PAGE_SIZE = 20
# Every collector stops here even if a site keeps claiming more pages.
MAX_PAGES = 25

_BAZOS_BLOCK_RE = re.compile(
    r'<div class="inzeratynadpis">.*?<h2 class=nadpis><a href="(?P<url>[^"]+)">(?P<title>.*?)</a>.*?'
    r'<div class="inzeratycena"><b><span[^>]*>(?P<price>[^<]*)</span>'
    r'(?:.*?<div class="inzeratyview">(?P<views>\d+)\s*x</div>)?',
    re.S,
)
_BAZOS_TOTAL_RE = re.compile(r"inzer\w*\s+z\s+(\d+)", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class CollectedListing(BaseModel):
    marketplace: str
    external_id: str
    title: str
    price_eur: Decimal
    url: str = ""
    # People who put the listing on their watchlist: the closest thing to a
    # named buyer that a marketplace will expose.
    watchers: int | None = None
    views: int | None = None


class SourceResult(BaseModel):
    marketplace: str
    ok: bool
    pages: int = 0
    reported_total: int | None = None
    listings: list[CollectedListing] = Field(default_factory=list)
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.listings)

    def complete(self) -> bool:
        """True when the site's own total matches what pagination returned."""
        return self.ok and (self.reported_total is None or self.reported_total == self.count)


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _price(text: str) -> Decimal:
    digits = re.sub(r"[^\d,.]", "", text).replace(",", ".")
    # Thousands separators leave more than one dot behind.
    if digits.count(".") > 1:
        digits = digits.replace(".", "", digits.count(".") - 1)
    try:
        return Decimal(digits) if digits else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def collect_bazos(query: str, settings: Settings) -> SourceResult:
    """Page through the Bazos search for the seller's phone number."""
    headers = {"User-Agent": settings.bazos_user_agent, "Accept": "text/html"}
    listings: dict[str, CollectedListing] = {}
    total: int | None = None
    pages = 0

    for page in range(MAX_PAGES):
        params = {"hledat": query, "rubriky": "www", "hlokalita": "", "humkreis": "25"}
        if page:
            params["crz"] = str(page * BAZOS_PAGE_SIZE)
        try:
            response = httpx.get(
                _BAZOS_SEARCH, params=params, headers=headers, timeout=30.0, follow_redirects=True
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            if listings:
                break
            return SourceResult(marketplace="bazos", ok=False, reason=str(exc))

        pages += 1
        body = response.text
        if total is None:
            found = _BAZOS_TOTAL_RE.search(body)
            total = int(found.group(1)) if found else None

        before = len(listings)
        for match in _BAZOS_BLOCK_RE.finditer(body):
            url = match.group("url")
            identifier = url.rsplit("/inzerat/", 1)[-1].split("/")[0]
            views = match.group("views")
            listings[identifier] = CollectedListing(
                marketplace="bazos",
                external_id=identifier,
                title=_clean(match.group("title")),
                price_eur=_price(match.group("price")),
                url=url,
                views=int(views) if views else None,
            )
        # A page that adds nothing new means the offset walked past the end.
        if len(listings) == before:
            break
        if total is not None and len(listings) >= total:
            break
        time.sleep(settings.bazos_request_gap_seconds)

    return SourceResult(
        marketplace="bazos",
        ok=True,
        pages=pages,
        reported_total=total,
        listings=list(listings.values()),
    )


def collect_aukro(seller_id: int, settings: Settings) -> SourceResult:
    """Page through the public Aukro offer search filtered to one seller id."""
    listings: dict[str, CollectedListing] = {}
    total: int | None = None
    pages = 0

    for page in range(MAX_PAGES):
        try:
            response = httpx.post(
                _AUKRO_SEARCH,
                params={"page": page, "size": 100},
                headers={
                    "User-Agent": settings.bazos_user_agent,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={"sellerId": seller_id},
                timeout=30.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if listings:
                break
            return SourceResult(marketplace="aukro", ok=False, reason=str(exc))

        pages += 1
        meta = payload.get("page") or {}
        total = meta.get("totalElements", total)
        content = payload.get("content") or []
        for node in content:
            price = node.get("buyNowPrice") or node.get("auctionPrice") or {}
            amount = Decimal(str(price.get("amount") or "0"))
            # Convert with the same dated rate as hunt/demand; do not infer the
            # storefront's whole-EUR price from a fixed historical multiplier.
            try:
                amount = Money(amount=amount, currency=str(price.get("currency") or "CZK")).to_eur(
                    settings.eur_czk, eur_pln=settings.eur_pln)
            except ValueError:
                return SourceResult(marketplace="aukro", ok=False,
                                    reason="No valid exchange rate; previous inventory retained")
            identifier = str(node.get("itemId") or "")
            if not identifier:
                continue
            watchers = node.get("watchersCount")
            listings[identifier] = CollectedListing(
                marketplace="aukro",
                external_id=identifier,
                title=str(node.get("itemName") or "").strip(),
                price_eur=amount,
                url=f"https://aukro.sk/{node.get('seoUrl', '')}-{identifier}",
                watchers=int(watchers) if isinstance(watchers, int) else None,
            )

        if not content or page + 1 >= int(meta.get("totalPages") or 1):
            break

    return SourceResult(
        marketplace="aukro",
        ok=True,
        pages=pages,
        reported_total=total,
        listings=list(listings.values()),
    )


def collect_ebay(seller: str, settings: Settings) -> SourceResult:
    """Page through the Browse API for the seller's own active listings."""
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        return SourceResult(
            marketplace="ebay",
            ok=False,
            reason="Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET; eBay blocks plain HTML scraping",
        )

    client = EbayBrowseClient(settings)
    headers = {
        "Authorization": f"Bearer {client._access_token()}",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace,
    }
    listings: dict[str, CollectedListing] = {}
    pages = 0

    # Browse API needs a category or query alongside the seller filter, so the
    # seller's categories are walked one by one.
    for category in rules()["ebay"]["small_categories"]:
        offset = 0
        for _ in range(MAX_PAGES):
            params = {
                "category_ids": category,
                "filter": f"sellers:{{{seller}}}",
                "limit": "200",
                "offset": str(offset),
            }
            try:
                response = httpx.get(_EBAY_SEARCH, headers=headers, params=params, timeout=30.0)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                return SourceResult(
                    marketplace="ebay", ok=False, pages=pages, reason=str(exc),
                    listings=list(listings.values()),
                )

            pages += 1
            summaries = payload.get("itemSummaries") or []
            for node in summaries:
                price = node.get("price") or {}
                identifier = str(node.get("itemId") or "")
                if not identifier:
                    continue
                watchers = node.get("watchCount")
                listings[identifier] = CollectedListing(
                    marketplace="ebay",
                    external_id=identifier,
                    title=str(node.get("title") or "").strip(),
                    price_eur=Decimal(str(price.get("value") or "0")),
                    url=str(node.get("itemWebUrl") or ""),
                    watchers=int(watchers) if isinstance(watchers, int) else None,
                )
            offset += len(summaries)
            if not summaries or offset >= int(payload.get("total") or 0):
                break

    return SourceResult(
        marketplace="ebay", ok=True, pages=pages, listings=list(listings.values())
    )


def collect_vinted(settings: Settings) -> SourceResult:
    """Page through the seller's own items via Vinted Pro Integrations.

    The public catalogue sits behind DataDome and this project does not bypass
    it, so the official sell-side API is the only supported route.
    """
    if not settings.vinted_access_key or not settings.vinted_signing_key:
        return SourceResult(
            marketplace="vinted",
            ok=False,
            reason=(
                "Set VINTED_ACCESS_KEY and VINTED_SIGNING_KEY; the public member "
                "page renders its items client-side behind DataDome"
            ),
        )

    client = VintedProClient(settings)
    listings: dict[str, CollectedListing] = {}
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        try:
            payload = client.list_own_items(page=page)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return SourceResult(
                marketplace="vinted", ok=False, pages=pages, reason=str(exc),
                listings=list(listings.values()),
            )
        pages += 1
        items = payload.get("items") or []
        for node in items:
            identifier = str(node.get("id") or "")
            if not identifier:
                continue
            listings[identifier] = CollectedListing(
                marketplace="vinted",
                external_id=identifier,
                title=str(node.get("title") or "").strip(),
                price_eur=Decimal(str((node.get("price") or {}).get("amount") or "0")),
                url=str(node.get("url") or ""),
            )
        if not items:
            break

    return SourceResult(
        marketplace="vinted", ok=True, pages=pages, listings=list(listings.values())
    )


def collect_all(settings: Settings | None = None) -> list[SourceResult]:
    config = settings or Settings()
    accounts = rules()["selling"]["accounts"]
    return [
        collect_bazos(str(accounts["bazos"]), config),
        collect_aukro(int(accounts["aukro_seller_id"]), config),
        collect_ebay(str(accounts["ebay"]), config),
        collect_vinted(config),
    ]


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(char for char in stripped if not unicodedata.combining(char)).lower()


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _fold(text))
    return {word for word in words if len(word) >= 3 or word.isdigit()}


def similarity(left: str, right: str) -> float:
    """Token overlap, which handles reordered and truncated titles."""
    first, second = tokens(left), tokens(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def closeness(left: str, right: str) -> float:
    """Character-level ratio, which survives the Slovak/Czech spelling drift.

    Aukro serves Czech translations of the Slovak originals, where 'vybrúsený a
    vyleštený' becomes 'broušený a leštěný'. Almost no whole token survives that,
    but most of the characters do.
    """
    return difflib.SequenceMatcher(None, _fold(left), _fold(right)).ratio()


def score_match(listing_title: str, item: InventoryItem) -> float:
    base = max(similarity(listing_title, item.title), closeness(listing_title, item.title))
    if not item.match_hints:
        return base
    folded = _fold(listing_title)
    if any(_fold(hint) in folded for hint in item.match_hints):
        return base
    # Near-identical variants (same chip, different year) only differ by their
    # hint, so a missing one has to outweigh an otherwise perfect title match.
    return base * 0.5


def match_listing(
    listing: CollectedListing, items: list[InventoryItem], *, threshold: float = 0.45
) -> InventoryItem | None:
    """Best fuzzy title match, so the same object is recognised across sites."""
    scored = [(score_match(listing.title, item), item) for item in items]
    best_score, best_item = max(scored, key=lambda pair: pair[0], default=(0.0, None))
    return best_item if best_score >= threshold else None


class RefreshReport(BaseModel):
    sources: list[SourceResult] = Field(default_factory=list)
    matched: int = 0
    updated: dict[str, int] = Field(default_factory=dict)
    unmatched: list[CollectedListing] = Field(default_factory=list)

    def summary(self) -> str:
        rows = []
        for source in self.sources:
            if source.ok:
                total = source.reported_total
                claim = f"/{total}" if total is not None else ""
                flag = "" if source.complete() else "  INCOMPLETE"
                rows.append(
                    f"  {source.marketplace:7} {source.count}{claim} listings "
                    f"over {source.pages} page(s){flag}"
                )
            else:
                rows.append(f"  {source.marketplace:7} skipped: {source.reason}")
        if self.unmatched:
            rows.append(f"  unmatched live listings: {len(self.unmatched)}")
        return "\n".join(rows)


def refresh_inventory(
    inventory: Inventory, results: list[SourceResult]
) -> tuple[Inventory, RefreshReport]:
    """Fold freshly paginated listings into the snapshot.

    A marketplace whose collector failed keeps its previous prices, so a missing
    credential never looks like a delisted item.
    """
    report = RefreshReport(sources=results)
    items = list(inventory.items)
    prices: dict[str, dict[str, Decimal]] = {item.id: {} for item in items}
    watchers: dict[str, dict[str, int]] = {item.id: {} for item in items}
    views: dict[str, dict[str, int]] = {item.id: {} for item in items}

    for source in results:
        if not source.ok:
            continue
        for listing in source.listings:
            matched = match_listing(listing, items)
            if matched is None:
                report.unmatched.append(listing)
                continue
            report.matched += 1
            prices[matched.id][source.marketplace] = listing.price_eur
            if listing.watchers is not None:
                watchers[matched.id][source.marketplace] = listing.watchers
            if listing.views is not None:
                views[matched.id][source.marketplace] = listing.views

    collected = {source.marketplace for source in results if source.ok}
    refreshed: list[InventoryItem] = []
    for item in items:
        listed = {
            marketplace: price
            for marketplace, price in item.listed.items()
            if marketplace not in collected
        }
        listed.update(prices[item.id])
        if listed != item.listed:
            report.updated[item.id] = len(listed)
        # Watch counts are only meaningful as of the last collection, so a
        # source that was skipped keeps whatever it last reported.
        watched = {k: v for k, v in item.watchers.items() if k not in collected}
        watched.update(watchers[item.id])
        seen = {k: v for k, v in item.views.items() if k not in collected}
        seen.update(views[item.id])
        refreshed.append(
            item.model_copy(
                update={
                    "listed": dict(sorted(listed.items())),
                    "watchers": dict(sorted(watched.items())),
                    "views": dict(sorted(seen.items())),
                }
            )
        )

    partial = sorted(
        {source.marketplace for source in results if not source.complete()}
    )
    return (
        inventory.model_copy(update={"items": refreshed, "partial": partial}),
        report,
    )
