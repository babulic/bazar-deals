from __future__ import annotations

from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical
from bazar_deals.htmlparse import parse_ebay_html
from bazar_deals.rules import rules

_EBAY = rules()["ebay"]
_TOKEN_URL = _EBAY["token_url"]
_SEARCH_URL = _EBAY["search_url"]
CONDITION_MAP = {key: Condition(value) for key, value in _EBAY["condition_map"].items()}
_SMALL_CATEGORIES = tuple(_EBAY["small_categories"])


class EbayBrowseClient(ListingSource):
    """Newest buy-now ebay.de listings under the price cap."""

    marketplace = Marketplace.EBAY.value

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._token: str | None = None

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        cap = str(self.settings.max_buy_eur)
        floor = str(self.settings.min_buy_eur)
        if self.settings.ebay_client_id and self.settings.ebay_client_secret:
            listings: list[Listing] = []
            for category in _SMALL_CATEGORIES:
                data = self.search(category, limit=30)
                listings.extend(self._to_listing(item) for item in data.get("itemSummaries", []))
            return [
                item
                for item in listings
                if item.buy_now
                and item.condition is not Condition.FOR_PARTS
                and "ebay.de" in str(item.url)
                and item.price.amount >= self.settings.min_buy_eur
                and item.price.amount <= self.settings.max_buy_eur
            ]
        return self._fetch_html(floor, cap)

    def _fetch_html(self, floor: str, cap: str) -> list[Listing]:
        url = (
            "https://www.ebay.de/sch/i.html?_sop=10&LH_BIN=1"
            f"&_udlo={floor}&_udhi={cap}&_ipg=60"
        )
        response = httpx.get(
            url,
            headers={
                "User-Agent": self.settings.bazos_user_agent,
                "Accept": "text/html",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"eBay public search HTML HTTP {response.status_code}")
        listings = [
            item
            for item in parse_ebay_html(response.text)
            if "ebay.de" in str(item.url)
            and item.price.amount >= self.settings.min_buy_eur
            and item.price.amount <= self.settings.max_buy_eur
        ]
        if not listings:
            raise RuntimeError("eBay Browse keys missing and public search HTML returned no items")
        return listings

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
                f"price:[{self.settings.min_buy_eur}..{self.settings.max_buy_eur}]"
            ),
        }
        response = httpx.get(_SEARCH_URL, headers=headers, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()

    def search_query(self, query: str, *, limit: int = 50) -> dict:
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
                f"price:[{self.settings.min_buy_eur}..{hi}]"
            ),
        }
        response = httpx.get(_SEARCH_URL, headers=headers, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()

    def _access_token(self) -> str:
        if self._token:
            return self._token
        if not self.settings.ebay_client_id or not self.settings.ebay_client_secret:
            raise RuntimeError("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET for Browse API")
        response = httpx.post(
            _TOKEN_URL,
            auth=(self.settings.ebay_client_id, self.settings.ebay_client_secret),
            data={
                "grant_type": "client_credentials",
                "scope": _EBAY["oauth_scope"],
            },
            timeout=20.0,
        )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    def _to_listing(self, item: dict) -> Listing:
        price = item.get("price") or {}
        condition_id = (item.get("conditionId") or item.get("condition") or "").upper()
        affiliate = item.get("itemAffiliateWebUrl") or None
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
            raw=item,
        )


def is_ebay_buy_now(item: dict) -> bool:
    if item.get("bidCount") or item.get("currentBidPrice"):
        return False
    options = {str(opt).upper() for opt in item.get("buyingOptions") or []}
    if "AUCTION" in options:
        return False
    if options and "FIXED_PRICE" not in options and "CLASSIFIED_AD" not in options:
        return False
    return True
