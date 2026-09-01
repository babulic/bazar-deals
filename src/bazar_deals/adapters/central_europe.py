"""Public classifieds and official Allegro search; never use private/login APIs."""
from __future__ import annotations

import html
import json
import re
import time
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlparse

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.adapters.allegro_auth import AllegroAuth, USER_AGENT
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money, Vertical
from bazar_deals.rules import rules

SITES = {
    "sbazar": "sbazar.cz",
    "facebook": "facebook.com",
    "allegro_pl": "allegro.pl",
    "allegro_sk": "allegro.sk",
    "olx": "olx.pl",
}
# Hourly hunt can fetch these. Facebook/OLX/Allegro stay sell/manual-only;
# probing them every hour only nags LOGIN_REQUIRED / ACCESS_NOT_GRANTED.
HUNT_SITES = ("sbazar",)


_WANT = re.compile(r"(?i)^\W*(?:kúpim|kupim|koupím|koupim|kupię|kupie|szukam|hľadám|hladam|hledám|hledam|wanted|wtb|looking for)\b")


def _exclude_demands(listings):
    return [item.model_copy(update={"buy_now": False}) if _WANT.match(item.title) else item for item in listings]


def search_url(source: str, query: str) -> str:
    if source == "sbazar":
        return f"https://www.sbazar.cz/hledej/{quote(query, safe='')}"
    if source == "olx":
        return f"https://www.olx.pl/oferty/q-{quote(query, safe='')}/"
    if source == "facebook":
        return "https://www.facebook.com/marketplace/bratislava/search/?" + urlencode({"query": query})
    return f"https://{SITES[source]}/listing?" + urlencode({"string": query, "delivery_to": "SK"})


def _safe_url(url: str, source: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and host in {SITES[source], "www." + SITES[source]})


def _money(amount: object, currency: str) -> Money | None:
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0:
            return None
        return Money(amount=value, currency=currency.upper())
    except (InvalidOperation, ValueError):
        return None


def delivery_to_sk(description: str) -> bool | None:
    """Only an explicit affirmative delivery sentence counts, never a country mention."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", description)).casefold()
    # Any restriction/question/conditional makes text evidence insufficient.
    if re.search(r"\b(ne\w*|nie\w*|not|no|only|pouze|jen|iba|tylko|pokud|ak|kdyby|mozna|možná)\b|\?", text):
        return None
    patterns = (
        r"(?:posílám|posilam|zasílám|zasilam|posielam|zasielam|pošlu|poslu)\s+(?:i\s+|aj\s+|také\s+)?(?:na\s+slovensko|do\s+sr)",
        r"(?:wysyłam|wysylam|wysyłka|wysylka)\s+(?:również\s+)?(?:na\s+słowację|do\s+słowacji)",
        r"(?:(?:i|we)\s+ship|ships?|shipping|delivery)\s+to\s+slovakia",
    )
    for sentence in re.split(r"[.!;\n]", text):
        if any(re.fullmatch(pattern, sentence.strip()) for pattern in patterns):
            return True
    return None


def _astro_decode(value):
    if isinstance(value, dict):
        return {key: _astro_decode(item) for key, item in value.items()}
    if isinstance(value, list) and value:
        if value[0] == 0:
            return _astro_decode(value[1]) if len(value) > 1 else None
        if value[0] == 1:
            return [_astro_decode(item) for item in value[1]]
    return value


class _PublicData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.astro = []
        self.products = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "astro-island" and "props" in attrs:
            try:
                self.astro.append(_astro_decode(json.loads(attrs["props"])))
            except (ValueError, TypeError, IndexError):
                pass
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try:
                self.products.append(json.loads("".join(self._script)))
            except ValueError:
                pass
            self._script = None


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _empty_sbazar_search(body: str) -> bool:
    """Sbazar serves genuine zero-result searches with HTTP 404, too."""
    data = _PublicData()
    data.feed(body)
    for props in data.astro:
        offers = props.get("offers") if isinstance(props, dict) else None
        if not isinstance(offers, dict) or offers.get("results") != []:
            continue
        pagination = offers.get("pagination")
        total = pagination.get("total") if isinstance(pagination, dict) else None
        if type(total) is int and total == 0:
            return True
    return False


def parse_public_listings(body: str, source: str) -> list[Listing]:
    data = _PublicData()
    data.feed(body)
    found = {}
    if source == "sbazar":
        for props in data.astro:
            # Do not import recommendations or other seller stock from a detail page.
            offers = (props.get("offers") or {}).get("results", [])
            if isinstance(props.get("offer"), dict):
                offers = [props["offer"]]
            for node in offers:
                slug = node.get("seoName", "")
                price = _money(node.get("price") if node.get("price") is not None else 0, "CZK")
                if not node.get("id") or not node.get("name") or not price or not slug:
                    continue
                description = str(node.get("description") or "")
                listing = Listing(
                    marketplace=Marketplace(source), external_id=str(node["id"]),
                    title=node["name"], description=description,
                    url=f"https://www.sbazar.cz/inzerat/{quote(slug, safe='-')}", price=price,
                    buy_now=not (node.get("sold") or node.get("isReserved") or node.get("priceByAgreement")),
                    location=str((node.get("locality") or {}).get("municipality") or ""),
                    ships_to_slovakia=delivery_to_sk(description),
                    raw={"delivery_evidence": description if delivery_to_sk(description) else ""},
                )
                found[listing.external_id] = listing
    for root in data.products:
        for node in _walk(root):
            if node.get("@type") != "Product":
                continue
            offers = node.get("offers") or []
            offers = offers if isinstance(offers, list) else [offers]
            # Multiple/aggregate offers do not establish an executable item price.
            if len(offers) != 1 or offers[0].get("@type") != "Offer":
                continue
            offer = offers[0]
            url = str(offer.get("url") or node.get("url") or "")
            if url.startswith("http://"):
                url = "https://" + url[7:]
            if not _safe_url(url, source) or not node.get("name"):
                continue
            price = _money(offer.get("price") if offer.get("price") is not None else 0,
                           offer.get("priceCurrency") or {"olx": "PLN", "sbazar": "CZK"}.get(source, "EUR"))
            if price is None or price.currency not in {"EUR", "CZK", "PLN"}:
                continue
            description = str(node.get("description") or "")
            eligible = delivery_to_sk(description)
            shipping = None
            details = offer.get("shippingDetails") or []
            for detail in details if isinstance(details, list) else [details]:
                destination = detail.get("shippingDestination") or {}
                if destination.get("addressCountry") == "SK" and detail.get("doesNotShip"):
                    eligible, shipping = False, None
                    break
                # Regional restrictions need checkout verification; country alone is insufficient then.
                if not detail.get("doesNotShip") and destination.get("addressCountry") == "SK" and not any(
                    destination.get(k) for k in ("addressRegion", "postalCode", "postalCodeRange")
                ):
                    eligible = True
                    rate = detail.get("shippingRate") or {}
                    shipping = _money(rate.get("value"), rate.get("currency") or price.currency)
            item_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            if source == "sbazar":
                item_id = item_id.split("-", 1)[0]
            elif source.startswith("allegro_"):
                item_id = item_id.rsplit("-", 1)[-1]
            listing = Listing(
                marketplace=Marketplace(source), external_id=item_id, title=node["name"],
                description=description, url=url, price=price,
                # Only current, in-stock fixed offers can reach BUY.
                buy_now=str(offer.get("availability", "")).rsplit("/", 1)[-1] == "InStock",
                ships_to_slovakia=eligible, shipping_cost=shipping,
                raw={"delivery_evidence": "offer shippingDetails SK" if shipping else description if eligible else ""},
            )
            found.setdefault(item_id, listing)
    return _exclude_demands(list(found.values()))


class CentralEuropeClient(ListingSource):
    def __init__(self, source: str, settings: Settings, *, client: httpx.Client | None = None):
        self.marketplace = source
        self.settings = settings
        self.client = client
        self.notes: list[str] = []
        self._auth = AllegroAuth(settings, client)

    def manual_mode(self) -> str | None:
        if self.marketplace == "facebook":
            return "LOGIN_REQUIRED: manual import only; browser login is not unattended API access"
        if self.marketplace == "olx":
            return "BLOCKED: manual import only; standard OLX API does not search other sellers"
        if self.marketplace.startswith("allegro_") and not self._auth.configured:
            return "ACCESS_NOT_GRANTED: authorized offers/listing access required; ALLEGRO_ACCESS_TOKEN alone does not grant permission; manual import available"
        return None

    def _get(self, url: str, **kwargs):
        # Never follow arbitrary listing redirects into another host/private address.
        requester = self.client.get if self.client else httpx.get
        response = requester(url, timeout=12, follow_redirects=False, **kwargs)
        if (response.status_code == 404 and self.marketplace == "sbazar"
                and url.startswith("https://www.sbazar.cz/hledej/")
                and _empty_sbazar_search(response.text)):
            return response
        # Only this fixed API endpoint may receive a refreshed bearer token.
        # 403 is an entitlement failure; refreshing cannot grant permissions.
        if response.status_code == 401 and url == "https://api.allegro.pl/offers/listing" and self._auth.automatic:
            kwargs["headers"] = {**kwargs.get("headers", {}), "Authorization": f"Bearer {self._auth.token(force=True)}"}
            response = requester(url, timeout=12, follow_redirects=False, **kwargs)
        if response.is_redirect:
            raise RuntimeError("LOGIN_REQUIRED: redirect/login required; manual verification needed")
        if response.status_code >= 400:
            status = "LOGIN_REQUIRED" if response.status_code == 401 else "ACCESS_NOT_GRANTED" if self.marketplace.startswith("allegro_") and response.status_code == 403 else "BLOCKED"
            raise RuntimeError(f"{status}: HTTP {response.status_code}; source unavailable (not an empty result)")
        return response

    def search(self, query: str) -> list[Listing]:
        if self.marketplace.startswith("allegro_"):
            return self._allegro(query)
        url = search_url(self.marketplace, query)
        response = self._get(url, headers={"User-Agent": self.settings.bazos_user_agent})
        listings = parse_public_listings(response.text, self.marketplace)
        if not listings:
            if self.marketplace == "sbazar" and _empty_sbazar_search(response.text):
                self.notes.append(f"sbazar: READY: no matches for {query}")
                return []
            raise RuntimeError(f"BLOCKED: no readable public listing data; check manually: {url}")
        return [item.model_copy(update={"search_query": query}) for item in listings]

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if reason := self.manual_mode():
            self.notes.append(f"{self.marketplace}: {reason}")
            return []
        config = rules()["central_europe"]
        queries = config["queries"].get(vertical.value if vertical else "all", [])
        found = {}
        for query in queries[:config["max_queries"]]:
            try:
                for listing in self.search(query):
                    found.setdefault(listing.external_id, listing)
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                self.notes.append(f"{self.marketplace}: {exc}")
                break  # Do not hammer a blocked or unauthenticated source.
            time.sleep(self.settings.bazos_request_gap_seconds)
        if found:
            self.notes.append(f"{self.marketplace}: READY: {len(found)} readable offers (SK eligibility checked separately)")
            unknown = sum(item.ships_to_slovakia is not True for item in found.values())
            if unknown:
                self.notes.append(f"{self.marketplace}: NEEDS_DELIVERY_CONFIRMATION: {unknown} offers require detail or manual evidence")
        return _exclude_demands(list(found.values()))

    def enrich_listing(self, listing: Listing) -> Listing:
        if listing.manual_import:
            return listing
        if not _safe_url(str(listing.url), self.marketplace):
            return listing
        try:
            response = self._get(str(listing.url), headers={"User-Agent": self.settings.bazos_user_agent})
            for detail in parse_public_listings(response.text, self.marketplace):
                if detail.external_id == listing.external_id:
                    return detail.model_copy(update={"search_query": listing.search_query})
        except (RuntimeError, httpx.HTTPError, ValueError):
            pass
        # Official destination-filtered Allegro results retain their delivery evidence.
        eligible = listing.ships_to_slovakia if self.marketplace.startswith("allegro_") else None
        return listing.model_copy(update={"ships_to_slovakia": eligible, "raw": {**listing.raw, "detail_fetched": False}})

    def _allegro(self, query: str) -> list[Listing]:
        if not self._auth.configured:
            raise RuntimeError(self.manual_mode() + "; manual search: " + search_url(self.marketplace, query))
        response = self._get(
            "https://api.allegro.pl/offers/listing",
            headers={"Authorization": f"Bearer {self._auth.token()}",
                     "Accept": "application/vnd.allegro.public.v1+json",
                     "User-Agent": USER_AGENT},
            params={"phrase": query, "marketplaceId": self.marketplace.replace("_", "-"),
                    "shipping.country": "SK", "currency": "EUR", "sellingMode.format": "BUY_NOW",
                    "sort": "-startTime", "limit": 60},
        )
        payload = response.json()
        if not isinstance(payload.get("items"), dict):
            raise RuntimeError("unrecognized Allegro listing response")
        found = {}
        for node in payload["items"].get("promoted", []) + payload["items"].get("regular", []):
            mode = node.get("sellingMode") or {}
            price_node = mode.get("price") or {}
            price = _money(price_node.get("amount"), price_node.get("currency") or "")
            item_id = str(node.get("id") or "")
            if not item_id.isdigit() or not node.get("name") or not price or price.currency != "EUR":
                continue
            delivery = node.get("delivery") or {}
            # lowestPrice is not necessarily postage to SK; retain the conservative reserve.
            found[item_id] = Listing(
                marketplace=Marketplace(self.marketplace), external_id=item_id, title=node["name"],
                url=f"https://{SITES[self.marketplace]}/{'oferta' if self.marketplace == 'allegro_pl' else 'ponuka'}/{item_id}", price=price,
                buy_now=mode.get("format") == "BUY_NOW", search_query=query,
                ships_to_slovakia=True,
                raw={"delivery_evidence": "official offers/listing shipping.country=SK", "delivery": delivery},
            )
        return _exclude_demands(list(found.values()))
