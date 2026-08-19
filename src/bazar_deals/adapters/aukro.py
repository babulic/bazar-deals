from __future__ import annotations

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Vertical

_API = "https://api.aukro.cz"


class AukroSellClient(ListingSource):
    """Aukro Public API is sell-side. No bid/buy automation in current REST API."""

    marketplace = Marketplace.AUKRO.value

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        raise RuntimeError(
            "Aukro Public API does not expose buy-side listing search. "
            "Use this client for automated selling after Aukro grants API access."
        )

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
