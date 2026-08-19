from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import SMALL_SEARCH_QUERIES, VERTICAL_KEYWORDS
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Vertical
from bazar_deals.htmlparse import parse_json_ld_products

_SEARCH = (
    "https://aukro.sk/vysledky-vyhladavania?text={query}"
    "&order=newest&sellingMode.format=BUY_NOW"
)
_API = "https://api.aukro.cz"


class AukroHuntClient(ListingSource):
    """Public Aukro search pages (JSON-LD). Sell API stays on AukroSellClient."""

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
        listings: list[Listing] = []
        for query in _queries(vertical):
            url = _SEARCH.format(query=quote(query))
            html = _get(url, self.settings.bazos_user_agent)
            listings.extend(
                parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
            )
            time.sleep(self.settings.bazos_request_gap_seconds)
        return listings


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


def _queries(vertical: Vertical | None) -> tuple[str, ...]:
    if vertical:
        return VERTICAL_KEYWORDS[vertical][:4]
    return SMALL_SEARCH_QUERIES[:6]


def _get(url: str, user_agent: str) -> str:
    response = httpx.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html"},
        timeout=30.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text
