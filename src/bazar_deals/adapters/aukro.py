from __future__ import annotations

import time
from pathlib import Path

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Vertical
from bazar_deals.htmlparse import parse_json_ld_products

_SEARCH = "https://aukro.sk/vysledky-vyhladavania?order=newest&sellingMode.format=BUY_NOW"
_API = "https://api.aukro.cz"


class AukroHuntClient(ListingSource):
    """Public Aukro newest buy-now pages with public detail enrichment."""

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
        found: list[Listing] = []
        seen: set[str] = set()
        for page in (1, 2):
            html = _get(f"{_SEARCH}&page={page}", self.settings.bazos_user_agent)
            batch = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
            for item in batch:
                key = item.external_id or str(item.url)
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
            if page == 1:
                time.sleep(min(2.0, max(0.0, self.settings.bazos_request_gap_seconds)))
        return found

    def enrich_listing(self, listing: Listing) -> Listing:
        if self.fixture_path or (listing.description or "").strip():
            return listing
        try:
            html = _get(str(listing.url), self.settings.bazos_user_agent)
        except httpx.HTTPError:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        products = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
        wanted_url = str(listing.url).split("?")[0].rstrip("/")
        detail = next(
            (
                item
                for item in products
                if str(item.url).split("?")[0].rstrip("/") == wanted_url
                and item.description.strip()
            ),
            None,
        )
        raw = dict(listing.raw)
        raw["detail_fetched"] = detail is not None
        if detail is None:
            return listing.model_copy(update={"raw": raw})
        return listing.model_copy(update={"description": detail.description, "raw": raw})


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
