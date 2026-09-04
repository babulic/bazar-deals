from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import hunt_research_only, hunt_fetch_queries
from bazar_deals.config import Settings
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical
from bazar_deals.htmlparse import parse_json_ld_products
from bazar_deals.rules import rules
from bazar_deals.working import is_damaged_text

_AUKRO = rules().get("aukro") or {}
_PUBLIC_SEARCH = str(_AUKRO.get("search_url") or "https://backend.aukro.cz/backend-web/api/offers/searchItemsCommon")
_SMALL_CATEGORIES = tuple(int(value) for value in _AUKRO.get("small_categories") or ())
_PAGE_SIZE = int(_AUKRO.get("page_size") or 30)
_PAGES = int(_AUKRO.get("pages") or 1)
_API = "https://api.aukro.cz"
_NG_STATE = re.compile(
    r'<script[^>]+id=["\']ng-state["\'][^>]*>(?P<payload>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _search_body(category_id: int | None) -> dict:
    body = {
        "fallbackItemsCount": 12,
        "splitGroupKey": "listing",
        "splitGroupValue": "A18",
    }
    if category_id is not None:
        body["categoryId"] = int(category_id)
    return body


class AukroHuntClient(ListingSource):
    """Aukro public web backend for active fixed-price offers."""

    marketplace = Marketplace.AUKRO.value

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_path: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.fixture_path = fixture_path

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if self.fixture_path:
            html = self.fixture_path.read_text(encoding="utf-8")
            return parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")

        found: dict[str, Listing] = {}
        targeted: dict[str, Listing] = {}
        gap = min(0.5, max(0.0, self.settings.bazos_request_gap_seconds))
        sku_queries = hunt_fetch_queries()
        for query in sku_queries:
            time.sleep(gap)
            try:
                for listing in self.search(query):
                    targeted[listing.external_id] = listing
            except (httpx.HTTPError, ValueError):
                continue
        if not hunt_research_only() and not sku_queries:
            categories = _SMALL_CATEGORIES or (None,)
            pages = _PAGES if _SMALL_CATEGORIES else 3
            for index, category_id in enumerate(categories):
                if index:
                    time.sleep(gap)
                for page in range(pages):
                    response = httpx.post(
                        _PUBLIC_SEARCH,
                        params={"page": page, "size": _PAGE_SIZE},
                        headers={
                            "User-Agent": self.settings.bazos_user_agent,
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        json=_search_body(category_id),
                        timeout=30.0,
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                    data = response.json()
                    for node in data.get("content") or []:
                        listing = _listing_from_public_node(node)
                        if listing is not None:
                            found[listing.external_id] = listing

        ordered = list(targeted.values())
        seen = set(targeted)
        for listing in found.values():
            if listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            ordered.append(listing)
        return ordered

    def search(self, query: str, *, size: int = 40) -> list[Listing]:
        """Current buy-now offers matching `query`, for the price book."""
        if self.fixture_path or not query.strip():
            return []
        response = httpx.post(
            _PUBLIC_SEARCH,
            params={"page": 0, "size": size},
            headers={
                "User-Agent": self.settings.bazos_user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                **_search_body(None),
                "text": query.strip(),
                "fallbackItemsCount": 12,
            },
            timeout=12.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        found: list[Listing] = []
        for node in response.json().get("content") or []:
            listing = _listing_from_public_node(node)
            if listing is not None:
                found.append(listing)
        return found

    def enrich_listing(self, listing: Listing) -> Listing:
        if self.fixture_path:
            return listing
        try:
            html = _get(str(listing.url), self.settings.bazos_user_agent)
        except httpx.HTTPError:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        products = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
        detail = next((item for item in products if item.description.strip()), None)
        attributes = _detail_attributes(html, listing.external_id)
        condition = _condition_from_attributes(attributes)
        raw = dict(listing.raw)
        raw["detail_fetched"] = detail is not None or bool(attributes)
        raw["condition_verified"] = condition is not Condition.UNKNOWN
        if attributes:
            raw["attributes"] = attributes
        updates: dict = {"raw": raw}
        if detail is not None:
            updates["description"] = detail.description
        if condition is not Condition.UNKNOWN:
            updates["condition"] = condition
        return listing.model_copy(update=updates)


def _detail_attributes(html: str, item_id: str) -> list[dict]:
    match = _NG_STATE.search(html)
    if match is None:
        return []
    try:
        state = json.loads(match.group("payload"))
    except (json.JSONDecodeError, TypeError):
        return []
    item = _find_item_with_attributes(state, item_id)
    if item is None:
        return []
    return [item for item in item.get("attributes") or [] if isinstance(item, dict)]


def _find_item_with_attributes(value: object, item_id: str) -> dict | None:
    if isinstance(value, dict):
        candidate_id = value.get("itemId") or value.get("id")
        if str(candidate_id or "") == str(item_id) and isinstance(value.get("attributes"), list):
            return value
        for child in value.values():
            found = _find_item_with_attributes(child, item_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_item_with_attributes(child, item_id)
            if found is not None:
                return found
    return None


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _condition_from_attributes(attributes: object) -> Condition:
    if not isinstance(attributes, list):
        return Condition.UNKNOWN
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        name = _fold(attribute.get("attributeName") or attribute.get("name"))
        if "stav" not in name and "condition" not in name:
            continue
        value = str(attribute.get("attributeValue") or attribute.get("value") or "")
        folded = _fold(value)
        if attribute.get("attributeValueId") == 4 or is_damaged_text(value):
            return Condition.FOR_PARTS
        if any(marker in folded for marker in ("nove", "new")):
            return Condition.NEW
        if any(marker in folded for marker in ("rozbalene", "zachovane", "like new")):
            return Condition.LIKE_NEW
        if any(marker in folded for marker in ("pouzite", "used")):
            return Condition.USED
    return Condition.UNKNOWN


def _listing_from_public_node(node: dict) -> Listing | None:
    if not isinstance(node, dict):
        return None
    if not node.get("buyNowActive") or node.get("auction") or node.get("adultContent"):
        return None
    item_id = str(node.get("itemId") or "")
    title = str(node.get("itemName") or "").strip()
    seo = str(node.get("seoUrl") or "").strip()
    price = node.get("buyNowPrice") if isinstance(node.get("buyNowPrice"), dict) else {}
    amount = Decimal(str(price.get("amount") or "0"))
    currency = str(price.get("currency") or "CZK")
    if not item_id or not title or not seo or amount <= 0:
        return None
    seller = node.get("seller") if isinstance(node.get("seller"), dict) else {}
    score = seller.get("positiveFeedbackPercentage")
    if not isinstance(score, (int, float)):
        score = None
    started = None
    try:
        started = datetime.fromisoformat(str(node.get("startingTime") or ""))
    except ValueError:
        pass
    attributes = node.get("attributes") if isinstance(node.get("attributes"), list) else []
    return Listing(
        marketplace=Marketplace.AUKRO,
        external_id=item_id,
        title=title,
        url=f"https://aukro.sk/{seo}-{item_id}",
        price=Money(amount=amount, currency=currency),
        condition=_condition_from_attributes(attributes),
        seller_id=str(node.get("sellerLogin") or "") or None,
        seller_score=float(score) if score is not None else None,
        created_at=started,
        buy_now=True,
        location=str(node.get("location") or "") or None,
        raw={
            "categoryPath": node.get("categoryPath"),
            "buyersProtectionAvailable": node.get("buyersProtectionAvailable"),
            "freeShipping": node.get("freeShipping"),
            "attributes": attributes,
            "condition_verified": _condition_from_attributes(attributes) is not Condition.UNKNOWN,
        },
    )


class AukroSellClient:
    """Aukro Public API is sell-side (create/manage own offers)."""

    marketplace = Marketplace.AUKRO.value

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def create_offer(self, payload: dict) -> dict:
        if not self.settings.aukro_api_token:
            raise RuntimeError("Set AUKRO_API_TOKEN after Aukro onboarding")
        response = httpx.post(
            f"{_API}/offers",
            headers={
                "Authorization": f"Bearer {self.settings.aukro_api_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()


def _get(url: str, user_agent: str) -> str:
    response = httpx.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html"},
        timeout=30.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text
