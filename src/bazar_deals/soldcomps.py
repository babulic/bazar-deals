from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Listing
from bazar_deals.htmlparse import parse_ebay_html
from bazar_deals.identity import similar_titles, sold_query
from bazar_deals.watchlist import MIN_SOLD_SAMPLE
from bazar_deals.working import is_damaged_text


@dataclass(frozen=True)
class SoldComp:
    median: Decimal
    sample: int
    label: str


class SoldCompClient:
    """Median of recent eBay.de *sold* prices for a tight query.

    Default path is public sold-search HTML (`LH_Sold=1&LH_Complete=1`).
    Keepa/Terapeak need paid keys — optional hook only, never required.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_html: str | None = None,
        fixture_path: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._cache: dict[str, list[Listing]] = {}
        self._fixture_html = fixture_html
        if fixture_path is not None:
            self._fixture_html = fixture_path.read_text(encoding="utf-8")

    def median_sold(self, listing: Listing) -> SoldComp | None:
        query = sold_query(f"{listing.title} {listing.description}")
        if not query:
            return None
        hay = f"{listing.title} {listing.description}"
        sold = [
            item
            for item in self._sold_hits(query)
            if item.price.amount > 0 and not is_damaged_text(item.title)
        ]
        peers = [item for item in sold if similar_titles(hay, item.title)]
        if len(peers) < MIN_SOLD_SAMPLE:
            return None
        amounts = sorted(item.price.amount for item in peers)
        mid = len(amounts) // 2
        if len(amounts) % 2:
            median = amounts[mid]
        else:
            median = ((amounts[mid - 1] + amounts[mid]) / 2).quantize(Decimal("0.01"))
        n = len(peers)
        return SoldComp(
            median=median,
            sample=n,
            label=f"obvyklá cena, funkčný kus, ebay.de sold (n={n})"
        )

    def _sold_hits(self, query: str) -> list[Listing]:
        if query in self._cache:
            return self._cache[query]
        if self._fixture_html is not None:
            hits = parse_ebay_html(self._fixture_html)
            self._cache[query] = hits
            return hits
        if self.settings.keepa_api_key:
            # Paid Keepa needs an ASIN map we do not have. Do not invent prices.
            pass
        url = (
            "https://www.ebay.de/sch/i.html?_nkw="
            f"{quote(query)}&LH_Sold=1&LH_Complete=1&_ipg=60"
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
        if response.status_code >= 400 or "signin.ebay." in str(response.url):
            self._cache[query] = []
            return []
        hits = parse_ebay_html(response.text)
        self._cache[query] = hits
        return hits
