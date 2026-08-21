from __future__ import annotations

import abc

from bazar_deals.domain import Listing, Vertical


class ListingSource(abc.ABC):
    marketplace: str

    @abc.abstractmethod
    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        """Return newly seen public listings. Must not use unofficial private APIs."""

    def enrich_listing(self, listing: Listing) -> Listing:
        """Optionally fetch the public item detail after cheap pre-filtering."""
        return listing
