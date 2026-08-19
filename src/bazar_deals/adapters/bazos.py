from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import BAZOS_RSS, SMALL_BAZOS_RUBS, VERTICAL_KEYWORDS, VERTICAL_RSS, is_bulky
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money, Vertical


class BazosRssClient(ListingSource):
    """Public Bazos RSS only. No unofficial GitHub 'private API' clients."""

    marketplace = Marketplace.BAZOS.value

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sites: tuple[str, ...] = ("sk", "cz"),
        fixture_path: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.sites = sites
        self.fixture_path = fixture_path

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if self.fixture_path:
            parsed = self._parse(self.fixture_path.read_text(encoding="utf-8"), site="sk")
            return [item for item in parsed if not is_bulky(f"{item.title} {item.description}")]

        listings: list[Listing] = []
        if vertical:
            params_list = VERTICAL_RSS.get(vertical, SMALL_BAZOS_RUBS)
        else:
            params_list = SMALL_BAZOS_RUBS
        for site in self.sites:
            base = BAZOS_RSS[site]
            for params in params_list:
                url = f"{base}?{urlencode(params)}" if params else base
                xml = self._get(url)
                listings.extend(self._parse(xml, site=site))
                time.sleep(self.settings.bazos_request_gap_seconds)
        listings = [item for item in listings if not is_bulky(f"{item.title} {item.description}")]
        if vertical:
            listings = [item for item in listings if _matches_vertical(item.title, vertical)]
        return listings

    def _get(self, url: str) -> str:
        headers = {"User-Agent": self.settings.bazos_user_agent, "Accept": "application/rss+xml"}
        response = httpx.get(url, headers=headers, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def _parse(self, xml: str, *, site: str) -> list[Listing]:
        feed = feedparser.parse(xml)
        currency = "EUR" if site == "sk" else "CZK"
        items: list[Listing] = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link")
            if not title or not link:
                continue
            price_amount, clean_title = _split_price(title)
            items.append(
                Listing(
                    marketplace=Marketplace.BAZOS,
                    external_id=str(entry.get("id") or link),
                    title=clean_title or title,
                    description=entry.get("summary", "") or "",
                    url=link,
                    price=Money(amount=price_amount, currency=currency),
                    created_at=_parse_date(entry),
                    raw={"site": site, "rss_title": title},
                )
            )
        return items


def _matches_vertical(title: str, vertical: Vertical) -> bool:
    hay = title.casefold()
    return any(keyword in hay for keyword in VERTICAL_KEYWORDS[vertical])


def _split_price(title: str) -> tuple[Decimal, str]:
    if " - " not in title:
        return Decimal("0"), title
    head, tail = title.rsplit(" - ", 1)
    digits = "".join(ch for ch in tail if ch.isdigit() or ch in ",.")
    digits = digits.replace(",", ".")
    try:
        amount = Decimal(digits) if digits else Decimal("0")
    except InvalidOperation:
        amount = Decimal("0")
    return amount, head.strip()


def _parse_date(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)
