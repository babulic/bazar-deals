from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, urlencode

import httpx

from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.catalog import BAZOS_RSS
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace
from bazar_deals.htmlparse import parse_ebay_html
from bazar_deals.identity import (
    ItemSpecs,
    classify_kind,
    extract_specs,
    listing_text,
    similar_titles,
    sold_query,
    with_specs,
)
from bazar_deals.rules import rules
from bazar_deals.working import is_damaged_text

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sold_listings (
    id INTEGER PRIMARY KEY,
    query_key TEXT NOT NULL,
    title TEXT NOT NULL,
    price_eur TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    sold_at TEXT,
    url TEXT,
    condition TEXT,
    fetched_at TEXT NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'ebay'
);
CREATE INDEX IF NOT EXISTS idx_sold_listings_query ON sold_listings(query_key);
CREATE TABLE IF NOT EXISTS sold_queries (
    query_key TEXT PRIMARY KEY,
    n INTEGER NOT NULL,
    median_eur TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL,
    http_status INTEGER
);
"""


@dataclass(frozen=True)
class SoldComp:
    median: Decimal
    sample: int
    label: str
    reliable_for_buy: bool = True


@dataclass(frozen=True)
class _QuerySummary:
    query_key: str
    n: int
    median: Decimal
    fetched_at: datetime
    source: str
    http_status: int | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _median(amounts: list[Decimal]) -> Decimal:
    ordered = sorted(amounts)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return ((ordered[mid - 1] + ordered[mid]) / 2).quantize(Decimal("0.01"))


def _lower_quartile(amounts: list[Decimal]) -> Decimal:
    """Conservative quick-sale price: nearest-rank 25th percentile."""
    ordered = sorted(amounts)
    if not ordered:
        return Decimal("0")
    index = max(0, (len(ordered) - 1) // 4)
    return ordered[index].quantize(Decimal("0.01"))


def _comp_label(n: int, source: str = "ebay") -> str:
    if source == "market":
        return f"asking-only kontrola trhu, nie BUY podklad (n={n})"
    return f"konzervatívna rýchlopredajná cena, ebay.de sold P25 (n={n})"


def _url_key(url: object) -> str:
    return str(url).split("?")[0].rstrip("/")


class SoldCompClient:
    """Conservative market value for goods that actually circulate.

    BUY valuation uses only a sufficiently large set of similar working sold
    eBay.de items and the lower quartile (P25), not the median. Current asking
    prices are kept only as a diagnostic fallback and are explicitly marked as
    unreliable for BUY decisions.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_html: str | None = None,
        fixture_path: Path | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._cache: dict[str, list[Listing]] = {}
        self._failed: dict[str, int] = {}
        self._asking_catalog: list[Listing] | None = None
        self.notes: list[str] = []
        self._noted: set[str] = set()
        self._sold_html_blocked = False
        self._sold_html_status: int | None = None
        self._browse_failed = False
        self._live_sold_used = 0
        self.live_sold_skipped = 0
        self._live_sold_budget = int(rules()["hunt"]["max_sold_lookups"])
        self._fixture_html = fixture_html
        if fixture_path is not None:
            self._fixture_html = fixture_path.read_text(encoding="utf-8")
        if self._fixture_html is not None:
            self._db_path: Path | None = None
        else:
            self._db_path = Path(db_path) if db_path is not None else Path(self.settings.comps_db)
            self._init_db()

    @property
    def sold_html_blocked(self) -> bool:
        return self._sold_html_blocked

    def _note(self, message: str) -> None:
        if not message or message in self._noted:
            return
        self._noted.add(message)
        self.notes.append(message)

    def seed_asking(self, listings: list[Listing]) -> None:
        """Use listings already fetched this hunt as the asking-price pool."""
        found: list[Listing] = []
        seen: set[str] = set()
        for item in listings:
            if item.price.amount <= 0:
                continue
            item = self._to_eur(item)
            key = _url_key(item.url)
            if key in seen:
                continue
            seen.add(key)
            found.append(item)
        self._asking_catalog = found

    def median_sold(
        self,
        listing: Listing,
        *,
        query: str | None = None,
        specs: ItemSpecs | None = None,
        subject: str | None = None,
    ) -> SoldComp | None:
        """Value one listing from completed sales.

        `query`, `specs` and `subject` let the caller pass an identity resolved
        with more context than this method has: a capacity found in the body of
        the ad, or the product name the AI recovered from an ad whose own title
        says nothing more than "Predám".
        """
        full_text = listing_text(listing)
        subject = (subject or "").strip() or listing.title
        query = (query or "").strip() or sold_query(subject) or sold_query(full_text)
        if not query:
            return None
        min_n = int(rules()["hunt"]["min_sold_sample"])
        # Specs are mined from the whole ad, similarity is measured on titles.
        # The lookup key must include those specs, otherwise a 128 GB phone and
        # a 256 GB phone (or a lot and a single piece) would share one P25.
        specs = specs if specs is not None else extract_specs(full_text)
        query = with_specs(query, specs)
        kind = classify_kind(full_text)
        self_key = _url_key(listing.url)
        ask_key = f"ask:{query}"

        sold_summary = self._db_summary(query)
        if sold_summary and self._is_fresh(sold_summary.fetched_at) and sold_summary.n >= min_n:
            return SoldComp(
                median=sold_summary.median,
                sample=sold_summary.n,
                label=_comp_label(sold_summary.n, "ebay"),
                reliable_for_buy=True,
            )

        hits, status, failed = self._sold_hits(query)
        if not failed:
            sold = [
                item
                for item in hits
                if item.price.amount > 0
                and not is_damaged_text(f"{item.title} {item.description}")
                and _url_key(item.url) != self_key
            ]
            # Marketplace descriptions are often long boilerplate. Variant and
            # capacity matching belongs on titles; including full descriptions
            # diluted Jaccard similarity enough to reject exact products.
            peers = [
                item
                for item in sold
                if similar_titles(subject, item.title, left_specs=specs, left_kind=kind)
            ]
            fetched_at = _utc_now()
            if self._db_path is not None:
                self._store_fetch(query, sold, peers, status, fetched_at, source="ebay")
            if len(peers) >= min_n:
                n = len(peers)
                return SoldComp(
                    median=_lower_quartile([item.price.amount for item in peers]),
                    sample=n,
                    label=_comp_label(n, "ebay"),
                    reliable_for_buy=True,
                )
        elif sold_summary and sold_summary.n >= min_n:
            return SoldComp(
                median=sold_summary.median,
                sample=sold_summary.n,
                label=_comp_label(sold_summary.n, "ebay"),
                reliable_for_buy=True,
            )

        ask_summary = self._db_summary(ask_key)
        if ask_summary and self._is_fresh(ask_summary.fetched_at) and ask_summary.n >= min_n:
            return SoldComp(
                median=ask_summary.median,
                sample=ask_summary.n,
                label=_comp_label(ask_summary.n, "market"),
                reliable_for_buy=False,
            )

        if self._fixture_html is not None:
            return None

        market = self._market_hits(query)
        asking = [
            item
            for item in market
            if item.price.amount > 0
            and not is_damaged_text(f"{item.title} {item.description}")
            and _url_key(item.url) != self_key
        ]
        peers = [
            item
            for item in asking
            if similar_titles(subject, item.title, left_specs=specs, left_kind=kind)
        ]
        if len(peers) < min_n:
            return None

        # Asking prices are not realized prices. Apply a strong haircut and, more
        # importantly, mark them unreliable so the pipeline cannot emit BUY.
        asking_value = (_lower_quartile([item.price.amount for item in peers]) * Decimal("0.75")).quantize(
            Decimal("0.01")
        )
        if self._db_path is not None:
            self._store_summary(ask_key, len(peers), asking_value, 200, _utc_now(), source="market")
        return SoldComp(
            median=asking_value,
            sample=len(peers),
            label=_comp_label(len(peers), "market"),
            reliable_for_buy=False,
        )

    def _sold_hits(self, query: str) -> tuple[list[Listing], int | None, bool]:
        if query in self._cache:
            return self._cache[query], self._failed.get(query), query in self._failed
        if self._sold_html_blocked:
            return [], self._sold_html_status, True
        if query in self._failed:
            return [], self._failed[query], True
        if self._fixture_html is not None:
            hits = parse_ebay_html(self._fixture_html)
            self._cache[query] = hits
            return hits, 200, False
        if self._live_sold_used >= self._live_sold_budget:
            self.live_sold_skipped += 1
            return [], None, True
        url = (
            "https://www.ebay.de/sch/i.html?_nkw="
            f"{quote(query)}&LH_Sold=1&LH_Complete=1&_ipg=60"
        )
        self._live_sold_used += 1
        response = httpx.get(
            url,
            headers={
                "User-Agent": self.settings.bazos_user_agent,
                "Accept": "text/html",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        status = int(response.status_code)
        signin = "signin.ebay." in str(response.url)
        if status >= 400 or signin:
            self._failed[query] = status
            if status in {401, 403} or signin:
                self._sold_html_blocked = True
                self._sold_html_status = status
                reason = "sign-in wall" if signin else f"HTTP {status}"
                self._note(
                    "ebay sold comps: "
                    f"{reason} from this host; Browse asking fallback needs working "
                    "EBAY_CLIENT_ID/SECRET (production App ID + Cert ID)"
                )
            return [], status, True
        hits = parse_ebay_html(response.text)
        self._cache[query] = hits
        return hits, status, False

    def _market_hits(self, query: str) -> list[Listing]:
        seen: set[str] = set()
        hits: list[Listing] = []
        for item in (
            *self._bazos_search(query),
            *self._public_asking_catalog(),
            *self._ebay_browse_search(query),
        ):
            item = self._to_eur(item)
            key = _url_key(item.url)
            if key in seen:
                continue
            seen.add(key)
            hits.append(item)
        return hits

    def _bazos_search(self, query: str) -> list[Listing]:
        url = f"{BAZOS_RSS['sk']}?{urlencode({'hledat': query})}"
        try:
            xml = self._get_text(url, accept="application/rss+xml")
        except httpx.HTTPError:
            return []
        return BazosRssClient(self.settings)._parse(xml, site="sk")

    def _public_asking_catalog(self) -> list[Listing]:
        """Fetch current Aukro/Vinted catalogs once per hunt, not once per query."""
        if self._asking_catalog is not None:
            return self._asking_catalog
        from bazar_deals.adapters.aukro import AukroHuntClient
        from bazar_deals.adapters.vinted import VintedHuntClient

        found: list[Listing] = []
        for client in (AukroHuntClient(self.settings), VintedHuntClient(self.settings)):
            try:
                found.extend(client.fetch_new())
            except (httpx.HTTPError, RuntimeError):
                continue
        self._asking_catalog = found
        return found

    def _to_eur(self, item: Listing) -> Listing:
        if item.price.currency.upper() == "EUR":
            return item
        return item.model_copy(
            update={
                "price": item.price.model_copy(
                    update={
                        "amount": item.price.to_eur(self.settings.eur_czk),
                        "currency": "EUR",
                    }
                )
            }
        )

    def _ebay_browse_search(self, query: str) -> list[Listing]:
        if self._browse_failed:
            return []
        if not self.settings.ebay_client_id or not self.settings.ebay_client_secret:
            return []
        from bazar_deals.adapters.ebay import EbayBrowseClient

        try:
            client = EbayBrowseClient(self.settings)
            data = client.search_query(query)
        except (httpx.HTTPError, RuntimeError) as exc:
            self._browse_failed = True
            self._note(f"ebay browse comps: {exc}")
            return []
        return [
            client._to_listing(item)
            for item in data.get("itemSummaries", [])
            if item.get("itemWebUrl") or item.get("itemHref")
        ]

    def _get_text(self, url: str, *, accept: str) -> str:
        response = httpx.get(
            url,
            headers={
                "User-Agent": self.settings.bazos_user_agent,
                "Accept": accept,
            },
            timeout=30.0,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {response.status_code}")
        return response.text

    def _is_fresh(self, fetched_at: datetime) -> bool:
        ttl = timedelta(days=max(0, int(self.settings.comps_ttl_days)))
        return _utc_now() - fetched_at <= ttl

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        assert self._db_path is not None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _db_summary(self, query_key: str) -> _QuerySummary | None:
        if self._db_path is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT query_key, n, median_eur, fetched_at, source, http_status "
                "FROM sold_queries WHERE query_key = ?",
                (query_key,),
            ).fetchone()
        if row is None:
            return None
        fetched = _parse_iso(str(row["fetched_at"]))
        if fetched is None:
            return None
        n = int(row["n"])
        median = _decimal(row["median_eur"])
        if n <= 0 or median <= 0:
            return None
        status = row["http_status"]
        return _QuerySummary(
            query_key=str(row["query_key"]),
            n=n,
            median=median,
            fetched_at=fetched,
            source=str(row["source"]),
            http_status=int(status) if status is not None else None,
        )

    def _store_summary(
        self,
        query_key: str,
        n: int,
        value: Decimal,
        status: int | None,
        fetched_at: datetime,
        *,
        source: str,
    ) -> None:
        if self._db_path is None:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sold_queries (query_key, n, median_eur, fetched_at, source, http_status) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(query_key) DO UPDATE SET "
                "n = excluded.n, median_eur = excluded.median_eur, fetched_at = excluded.fetched_at, "
                "source = excluded.source, http_status = excluded.http_status",
                (query_key, n, str(value), _iso(fetched_at), source, status),
            )

    def _store_fetch(
        self,
        query_key: str,
        sold: list[Listing],
        peers: list[Listing],
        status: int | None,
        fetched_at: datetime,
        *,
        source: str = "ebay",
    ) -> None:
        if self._db_path is None:
            return
        stamp = _iso(fetched_at)
        value = _lower_quartile([item.price.amount for item in peers]) if peers else Decimal("0")
        with self._connect() as conn:
            conn.execute("DELETE FROM sold_listings WHERE query_key = ?", (query_key,))
            conn.executemany(
                "INSERT INTO sold_listings "
                "(query_key, title, price_eur, currency, sold_at, url, condition, fetched_at, marketplace) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        query_key,
                        item.title,
                        str(item.price.amount),
                        item.price.currency,
                        _iso(item.created_at) if item.created_at else None,
                        str(item.url),
                        item.condition.value if item.condition else None,
                        stamp,
                        item.marketplace.value,
                    )
                    for item in sold
                ],
            )
        if peers:
            self._store_summary(query_key, len(peers), value, status, fetched_at, source=source)
