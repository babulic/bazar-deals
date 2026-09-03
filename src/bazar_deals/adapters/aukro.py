from __future__ import annotations

import json
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import hunt_research_only, hunt_fetch_queries
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money, Vertical
from bazar_deals.htmlparse import parse_json_ld_products
from bazar_deals.rules import rules

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
        if self.fixture_path or ((listing.description or "").strip() and listing.shipping_cost is not None):
            return listing
        try:
            html = _get(str(listing.url), self.settings.bazos_user_agent)
        except httpx.HTTPError:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        products = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
        detail = next((item for item in products if item.description.strip()), None)
        ships_to_slovakia, shipping, delivery_evidence = _aukro_sk_shipping(
            html,
            listing.external_id,
            self.settings,
        )
        raw = dict(listing.raw)
        raw["detail_fetched"] = detail is not None or ships_to_slovakia is not None
        if delivery_evidence:
            raw["delivery_evidence"] = delivery_evidence
        updates: dict = {"raw": raw}
        if detail is not None:
            updates["description"] = detail.description
        if ships_to_slovakia is not None:
            updates["ships_to_slovakia"] = ships_to_slovakia
        if shipping is not None:
            updates["shipping_cost"] = shipping
        return listing.model_copy(update=updates)


def _aukro_sk_shipping(
    html: str,
    item_id: str,
    settings: Settings,
) -> tuple[bool | None, Money | None, str]:
    """Read the exact Slovakia delivery options embedded in Aukro's public item page."""
    match = _NG_STATE.search(html)
    if match is None:
        return None, None, ""
    try:
        state = json.loads(match.group("payload"))
    except (json.JSONDecodeError, TypeError):
        return None, None, ""
    item = _find_shipping_item(state, item_id)
    if item is None:
        return None, None, ""

    options = item.get("shippingOptions")
    if not isinstance(options, list):
        return None, None, ""
    sk_options: list[tuple[Decimal, Money, str]] = []
    has_sk_destination = False
    for option in options:
        if not isinstance(option, dict) or str(option.get("countryTo") or "").upper() != "SK":
            continue
        has_sk_destination = True
        price = option.get("firstPackagePrice")
        if option.get("freeOfCharge") is True:
            money = Money(amount=Decimal("0"), currency="EUR")
        elif isinstance(price, dict) and price.get("amount") is not None:
            try:
                money = Money(
                    amount=Decimal(str(price["amount"])),
                    currency=str(price.get("currency") or "EUR"),
                )
            except (ArithmeticError, ValueError):
                continue
        else:
            continue
        try:
            eur = money.to_eur(settings.eur_czk, eur_pln=settings.eur_pln)
        except ValueError:
            continue
        method = str(option.get("name") or option.get("method") or "Aukro delivery")
        sk_options.append((eur, money, method))

    if not has_sk_destination:
        return False, None, "Aukro shippingOptions: no delivery option with countryTo=SK"
    if not sk_options:
        return True, None, "Aukro shippingOptions: countryTo=SK; exact price unavailable"
    _, cheapest, method = min(sk_options, key=lambda row: row[0])
    evidence = (
        f"Aukro shippingOptions: countryTo=SK, {method}, "
        f"{cheapest.amount} {cheapest.currency}"
    )
    return True, cheapest, evidence


def _find_shipping_item(value: object, item_id: str) -> dict | None:
    if isinstance(value, dict):
        if str(value.get("itemId") or "") == str(item_id) and isinstance(value.get("shippingOptions"), list):
            return value
        for child in value.values():
            found = _find_shipping_item(child, item_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_shipping_item(child, item_id)
            if found is not None:
                return found
    return None


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
    return Listing(
        marketplace=Marketplace.AUKRO,
        external_id=item_id,
        title=title,
        url=f"https://aukro.sk/{seo}-{item_id}",
        price=Money(amount=amount, currency=currency),
        seller_id=str(node.get("sellerLogin") or "") or None,
        seller_score=float(score) if score is not None else None,
        created_at=started,
        buy_now=True,
        location=str(node.get("location") or "") or None,
        raw={
            "categoryPath": node.get("categoryPath"),
            "buyersProtectionAvailable": node.get("buyersProtectionAvailable"),
            "freeShipping": node.get("freeShipping"),
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
