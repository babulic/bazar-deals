from __future__ import annotations

from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import hunt_research_only, hunt_target_queries
from bazar_deals.config import Settings
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical
from bazar_deals.rules import rules

_EBAY = rules()["ebay"]
_TOKEN_URL = _EBAY["token_url"]
_SEARCH_URL = _EBAY["search_url"]
CONDITION_MAP = {key: Condition(value) for key, value in _EBAY["condition_map"].items()}
_SMALL_CATEGORIES = tuple(_EBAY["small_categories"])


class EbayBrowseClient(ListingSource):
    """Newest buy-now ebay.de listings that can be delivered to Slovakia."""

    marketplace = Marketplace.EBAY.value

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._client = client
        self._token: str | None = None

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if not self.settings.ebay_client_id or not self.settings.ebay_client_secret:
            raise RuntimeError(
                "eBay Browse API credentials are required: public HTML cannot reliably confirm delivery to Slovakia"
            )
        listings: list[Listing] = []
        if not hunt_research_only():
            for category in _SMALL_CATEGORIES:
                data = self.search(category, limit=30)
                listings.extend(self._to_listing(item) for item in data.get("itemSummaries", []))
        seen = {item.external_id for item in listings}
        for query in hunt_target_queries():
            try:
                data = self.search_query(query, limit=30)
            except (httpx.HTTPError, RuntimeError, ValueError):
                continue
            for item in data.get("itemSummaries", []):
                listing = self._to_listing(item)
                if listing.external_id in seen:
                    continue
                seen.add(listing.external_id)
                listings.append(listing)
        return [
            item
            for item in listings
            if item.buy_now
            and item.condition is not Condition.FOR_PARTS
            and item.ships_to_slovakia is True
            and "ebay.de" in str(item.url)
            and item.price.amount >= self.settings.min_buy_eur
            and item.price.amount <= self.settings.max_buy_eur
        ]

    def enrich_listing(self, listing: Listing) -> Listing:
        href = str(listing.raw.get("itemHref") or "").strip()
        if not href:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace,
        }
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

    def search(self, category_id: str, *, limit: int = 50) -> dict:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace,
        }
        if self.settings.ebay_campaign_id:
            headers["X-EBAY-C-ENDUSERCTX"] = (
                f"affiliateCampaignId={self.settings.ebay_campaign_id}"
            )
        params = {
            "category_ids": category_id,
            "sort": "newlyListed",
            "limit": str(limit),
            "filter": (
                "buyingOptions:{FIXED_PRICE},"
                "conditions:{NEW|USED},"
                "deliveryCountry:SK,"
                f"price:[{self.settings.min_buy_eur}..{self.settings.max_buy_eur}]"
            ),
        }
        response = httpx.get(_SEARCH_URL, headers=headers, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()

    def search_query(self, query: str, *, limit: int = 50, purchase_budget: bool = True) -> dict:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace,
        }
        hi = self.settings.max_buy_eur * 3
        params = {
            "q": query,
            "sort": "newlyListed",
            "limit": str(limit),
            "filter": (
                "buyingOptions:{FIXED_PRICE},"
                "conditions:{NEW|USED},"
                "deliveryCountry:SK"
                + (f",price:[{self.settings.min_buy_eur}..{hi}]" if purchase_budget else "")
            ),
        }
        response = httpx.get(_SEARCH_URL, headers=headers, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()

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

    def _to_listing(self, item: dict) -> Listing:
        price = item.get("price") or {}
        condition_id = (item.get("conditionId") or item.get("condition") or "").upper()
        affiliate = item.get("itemAffiliateWebUrl") or None
        shipping = _shipping_cost(item)
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
            raw=item,
        )


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
