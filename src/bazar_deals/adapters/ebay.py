from __future__ import annotations

import time
from collections import Counter
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import hunt_research_only, hunt_fetch_queries
from bazar_deals.config import Settings
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical
from bazar_deals.rules import rules

_EBAY = rules()["ebay"]
_TOKEN_URL = _EBAY["token_url"]
_SEARCH_URL = _EBAY["search_url"]
CONDITION_MAP = {key: Condition(value) for key, value in _EBAY["condition_map"].items()}
_SMALL_CATEGORIES = tuple(_EBAY["small_categories"])
_HUNT_MARKETPLACES = tuple(
    str(item) for item in (_EBAY.get("hunt_marketplace_ids") or [_EBAY["marketplace_id"]])
)
_HUNT_HOSTS = ("ebay.de", "ebay.at")
_EBAY_RETRY_BUDGET = 1


def hunt_ebay_marketplace_ids() -> tuple[str, ...]:
    return _HUNT_MARKETPLACES or ("EBAY_DE",)


def is_hunt_ebay_url(url: object) -> bool:
    """True for the German and Austrian storefronts the hourly hunt is allowed to buy from."""
    host = (urlparse(str(url)).hostname or "").casefold()
    return any(host == item or host.endswith("." + item) for item in _HUNT_HOSTS)


def ebay_listing_host(url: object) -> str:
    host = (urlparse(str(url)).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def marketplace_id_for_url(url: object) -> str:
    host = ebay_listing_host(url)
    if host.endswith("ebay.at"):
        return "EBAY_AT"
    return "EBAY_DE"


class EbayBrowseClient(ListingSource):
    """Newest buy-now ebay.de and ebay.at listings that can be delivered to Slovakia."""

    marketplace = Marketplace.EBAY.value

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        *,
        retry_budget: int = _EBAY_RETRY_BUDGET,
    ) -> None:
        self.settings = settings or Settings()
        self._client = client
        self._token: str | None = None
        self._retry_budget = max(0, retry_budget)
        self.notes: list[str] = []

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if not self.settings.ebay_client_id or not self.settings.ebay_client_secret:
            raise RuntimeError(
                "eBay Browse API credentials are required: public HTML cannot reliably confirm delivery to Slovakia"
            )
        listings: list[Listing] = []
        seen: set[str] = set()
        last_exc: BaseException | None = None
        self.notes = []
        globally_throttled = False
        for marketplace_id in hunt_ebay_marketplace_ids():
            if globally_throttled:
                break
            throttled = False
            seen_before = len(seen)
            for query in hunt_fetch_queries():
                if throttled:
                    break
                try:
                    data = self.search_query(query, limit=30, marketplace_id=marketplace_id)
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response is not None and exc.response.status_code == 429:
                        throttled = True
                        globally_throttled = True
                        self.notes.append(
                            "ebay: RATE_LIMITED: Browse API HTTP 429 after retries; "
                            "all remaining eBay searches stopped until quota reset"
                        )
                    continue
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    last_exc = exc
                    continue
                self._ingest(data, listings, seen, marketplace_id)
            # Category newest-dumps 429 the Browse API. Only use them when SKU
            # search returned nothing for this storefront.
            if not hunt_research_only() and not throttled and len(seen) == seen_before:
                for category in _SMALL_CATEGORIES:
                    try:
                        data = self.search(category, limit=30, marketplace_id=marketplace_id)
                    except httpx.HTTPStatusError as exc:
                        last_exc = exc
                        if exc.response is not None and exc.response.status_code == 429:
                            globally_throttled = True
                            self.notes.append(
                                "ebay: RATE_LIMITED: Browse API HTTP 429 after retries; "
                                "all remaining eBay searches stopped until quota reset"
                            )
                            break
                        continue
                    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                        last_exc = exc
                        continue
                    self._ingest(data, listings, seen, marketplace_id)
        found = [
            item
            for item in listings
            if item.buy_now
            and item.condition is not Condition.FOR_PARTS
            and item.ships_to_slovakia is True
            and is_hunt_ebay_url(item.url)
            and item.price.amount >= self.settings.min_buy_eur
            and item.price.amount <= self.settings.max_buy_eur
        ]
        hosts = Counter(ebay_listing_host(item.url) for item in found)
        for host in _HUNT_HOSTS:
            self.notes.append(f"{host}: fetched {hosts.get(host, 0)}")
        if not found and last_exc is not None:
            raise last_exc
        return found

    def _ingest(
        self,
        data: dict,
        listings: list[Listing],
        seen: set[str],
        marketplace_id: str,
    ) -> None:
        for item in data.get("itemSummaries", []):
            listing = self._to_listing(item, marketplace_id=marketplace_id)
            if listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            listings.append(listing)

    def enrich_listing(self, listing: Listing) -> Listing:
        href = str(listing.raw.get("itemHref") or "").strip()
        if not href:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        headers = self._browse_headers(
            str(listing.raw.get("ebay_marketplace") or marketplace_id_for_url(listing.url))
        )
        try:
            response = httpx.get(href, headers=headers, timeout=20.0)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        description = str(data.get("description") or data.get("shortDescription") or "").strip()
        shipping = _shipping_cost(data) or listing.shipping_cost
        raw = dict(listing.raw)
        raw["detail_fetched"] = bool(description) or bool(data.get("localizedAspects"))
        raw["detail"] = data
        for key in ("shortDescription", "localizedAspects", "brand", "mpn"):
            if data.get(key) not in (None, "", []):
                raw[key] = data[key]
        return listing.model_copy(
            update={
                "description": description,
                "shipping_cost": shipping,
                "raw": raw,
            }
        )

    def _browse_headers(self, marketplace_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id or self.settings.ebay_marketplace,
        }
        if self.settings.ebay_campaign_id:
            headers["X-EBAY-C-ENDUSERCTX"] = (
                f"affiliateCampaignId={self.settings.ebay_campaign_id}"
            )
        return headers

    def search(
        self, category_id: str, *, limit: int = 50, marketplace_id: str | None = None
    ) -> dict:
        params = {
            "category_ids": category_id,
            "sort": "newlyListed",
            "limit": str(limit),
            "filter": browse_filter(
                min_price=self.settings.min_buy_eur,
                max_price=self.settings.max_buy_eur,
            ),
        }
        return self._browse_get(params, marketplace_id)

    def search_query(
        self,
        query: str,
        *,
        limit: int = 50,
        purchase_budget: bool = True,
        marketplace_id: str | None = None,
    ) -> dict:
        hi = self.settings.max_buy_eur * 3
        params = {
            "q": query,
            "sort": "newlyListed",
            "limit": str(limit),
            "filter": browse_filter(
                min_price=self.settings.min_buy_eur if purchase_budget else None,
                max_price=hi if purchase_budget else None,
            ),
        }
        return self._browse_get(params, marketplace_id)

    def _browse_get(self, params: dict, marketplace_id: str | None) -> dict:
        retries = self._retry_budget
        headers = self._browse_headers(marketplace_id)
        while True:
            if self._client is not None:
                response = self._client.get(_SEARCH_URL, headers=headers, params=params)
            else:
                response = httpx.get(
                    _SEARCH_URL, headers=headers, params=params, timeout=20.0
                )
            if response.status_code == 429 and retries > 0:
                retries -= 1
                self._retry_wait(_retry_after_seconds(response))
                continue
            response.raise_for_status()
            return response.json()

    def _retry_wait(self, seconds: float) -> None:
        if self._client is not None or self.settings.bazos_request_gap_seconds <= 0:
            return
        time.sleep(max(1.0, min(seconds, 45.0)))

    def _access_token(self) -> str:
        if not self.settings.ebay_retention_enabled:
            raise RuntimeError(
                "eBay is in no-persistence test mode; use python -m bazar_deals.ebay_probe. "
                "Regular imports and reports are disabled while the exemption applies."
            )
        if self._token:
            return self._token
        if not self.settings.ebay_client_id or not self.settings.ebay_client_secret:
            raise RuntimeError("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET for Browse API")
        payload = {
            "grant_type": "client_credentials",
            "scope": _EBAY["oauth_scope"],
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = (self.settings.ebay_client_id, self.settings.ebay_client_secret)
        if self._client is not None:
            response = self._client.post(_TOKEN_URL, auth=auth, data=payload, headers=headers)
        else:
            response = httpx.post(
                _TOKEN_URL, auth=auth, data=payload, headers=headers, timeout=20.0
            )
        if response.status_code >= 400:
            raise RuntimeError(_oauth_reject_message(response))
        try:
            self._token = response.json()["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"eBay OAuth response missing access_token ({exc})") from exc
        return self._token

    def _to_listing(self, item: dict, *, marketplace_id: str | None = None) -> Listing:
        price = item.get("price") or {}
        condition_id = (item.get("conditionId") or item.get("condition") or "").upper()
        affiliate = item.get("itemAffiliateWebUrl") or None
        shipping = _shipping_cost(item)
        raw = dict(item)
        if marketplace_id:
            raw["ebay_marketplace"] = marketplace_id
        return Listing(
            marketplace=Marketplace.EBAY,
            external_id=item.get("itemId", ""),
            title=item.get("title", ""),
            url=item.get("itemWebUrl") or item.get("itemHref") or "https://www.ebay.de/",
            price=Money(
                amount=Decimal(str(price.get("value", "0"))),
                currency=str(price.get("currency") or "EUR"),
            ),
            condition=CONDITION_MAP.get(condition_id, Condition.UNKNOWN),
            seller_id=(item.get("seller") or {}).get("username"),
            affiliate_url=affiliate,
            bid_count=item.get("bidCount"),
            buy_now=is_ebay_buy_now(item),
            ships_to_slovakia=True,
            shipping_cost=shipping,
            raw=raw,
        )


def _retry_after_seconds(response: httpx.Response) -> float:
    header = str(response.headers.get("Retry-After") or response.headers.get("retry-after") or "")
    try:
        wait = float(header)
    except ValueError:
        wait = 4.0
    return max(1.0, min(wait, 45.0))


def browse_filter(*, min_price=None, max_price=None) -> str:
    """Browse API search filter that eBay accepts for SK delivery.

    `price:[lo..hi]` without `priceCurrency` is a 400. `conditions:{NEW|USED}`
    is not a valid Browse filter (use `conditionIds` if you need it) and a
    single bad category used to abort the whole eBay hunt.
    """
    parts = ["buyingOptions:{FIXED_PRICE}", "deliveryCountry:SK"]
    if min_price is not None and max_price is not None:
        lo = int(min_price)
        hi = int(max_price)
        parts.append(f"price:[{lo}..{hi}]")
        parts.append("priceCurrency:EUR")
    return ",".join(parts)


def _oauth_reject_message(response: httpx.Response) -> str:
    """401 here means the secrets reached eBay and were refused, not that they are missing."""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("error_description") or payload.get("error") or "").strip()
    except ValueError:
        detail = (response.text or "").strip()[:300]
    hint = (
        "credentials are set but eBay rejected them — check keyset activation, OAuth "
        "and Marketplace Account Deletion compliance, then verify the production "
        "App ID (Client ID) and Cert ID (Client Secret), not sandbox and not Dev ID"
    )
    if detail:
        return f"eBay OAuth {response.status_code}: {detail}. {hint}"
    return f"eBay OAuth {response.status_code}. {hint}"


def _shipping_cost(item: dict) -> Money | None:
    options = item.get("shippingOptions") or []
    costs: list[Money] = []
    for option in options:
        value = option.get("shippingCost") or {}
        if value.get("value") is None:
            continue
        try:
            amount = Decimal(str(value["value"]))
        except Exception:
            continue
        costs.append(Money(amount=amount, currency=str(value.get("currency") or "EUR")))
    if not costs:
        return None
    return min(costs, key=lambda money: money.amount)


def is_ebay_buy_now(item: dict) -> bool:
    if item.get("bidCount") or item.get("currentBidPrice"):
        return False
    options = {str(opt).upper() for opt in item.get("buyingOptions") or []}
    if "AUCTION" in options:
        return False
    if options and "FIXED_PRICE" not in options and "CLASSIFIED_AD" not in options:
        return False
    return True
