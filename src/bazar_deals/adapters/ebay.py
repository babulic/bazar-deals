from __future__ import annotations

from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

CONDITION_MAP = {
    "NEW": Condition.NEW,
    "NEW_OTHER": Condition.LIKE_NEW,
    "CERTIFIED_REFURBISHED": Condition.LIKE_NEW,
    "USED_EXCELLENT": Condition.LIKE_NEW,
    "USED_VERY_GOOD": Condition.USED,
    "USED_GOOD": Condition.USED,
    "USED_ACCEPTABLE": Condition.USED,
    "FOR_PARTS_OR_NOT_WORKING": Condition.FOR_PARTS,
}


class EbayBrowseClient(ListingSource):
    """eBay Browse API: search + newly listed sort + EPN affiliate URLs."""

    marketplace = Marketplace.EBAY.value

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._token: str | None = None

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        query = {
            Vertical.RETRO: "commodore,amiga,atari,nintendo",
            Vertical.APPLE: "macbook,iphone,ipad",
            Vertical.NETWORK: "mikrotik,unifi,cisco switch",
            Vertical.MINERAL: "mineral specimen amethyst",
        }.get(vertical, "vintage computer")
        data = self.search(query, sort="newlyListed", limit=50)
        return [self._to_listing(item) for item in data.get("itemSummaries", [])]

    def search(self, query: str, *, sort: str = "newlyListed", limit: int = 50) -> dict:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace,
        }
        if self.settings.ebay_campaign_id:
            headers["X-EBAY-C-ENDUSERCTX"] = (
                f"affiliateCampaignId={self.settings.ebay_campaign_id}"
            )
        params = {"q": query, "sort": sort, "limit": str(limit)}
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
                "scope": "https://api.ebay.com/oauth/api_scope",
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
            url=item.get("itemWebUrl") or item.get("itemHref") or "https://www.ebay.com/",
            price=Money(
                amount=Decimal(str(price.get("value", "0"))),
                currency=str(price.get("currency") or "EUR"),
            ),
            condition=CONDITION_MAP.get(condition_id, Condition.UNKNOWN),
            seller_id=(item.get("seller") or {}).get("username"),
            affiliate_url=affiliate,
            raw=item,
        )
