from __future__ import annotations

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlencode

import httpx

from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.adapters.central_europe import SITES
from bazar_deals.catalog import BAZOS_RSS
from bazar_deals.config import Settings
from bazar_deals.domain import Listing
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

_PRICE_BOOK_VERSION = "product-role-v2:"

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
class PriceBookMiss:
    """A listing we tried to value but did not have five comparable ads."""

    listing: Listing
    query: str
    peer_count: int
    required: int
    typical: Decimal | None
    peers: tuple[Listing, ...] = ()


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


def _lower_quartile(amounts: list[Decimal]) -> Decimal:
    """Conservative quick-sale price: nearest-rank 25th percentile."""
    ordered = sorted(amounts)
    if not ordered:
        return Decimal("0")
    index = max(0, (len(ordered) - 1) // 4)
    return ordered[index].quantize(Decimal("0.01"))


def _comp_label(n: int, source: str = "market") -> str:
    if source in {"market", "ask"}:
        return f"trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n={n})"
    return f"konzervatívna rýchlopredajná cena, ebay.de sold P25 (n={n})"


def _url_key(url: object) -> str:
    parsed = urlparse(str(url))
    if parsed.hostname in {"allegro.pl", "www.allegro.pl", "allegro.sk", "www.allegro.sk"}:
        match = re.search(r"(?:/|-)(\d+)/?$", parsed.path)
        if match:
            return f"allegro:{match.group(1)}"
    return str(url).split("?")[0].rstrip("/")


def _market_value(peers: list[Listing]) -> Decimal:
    if not peers:
        return Decimal("0")
    return (_lower_quartile([item.price.amount for item in peers]) * Decimal("0.75")).quantize(
        Decimal("0.01")
    )


class SoldCompClient:
    """Price book of discovered comparable asking prices.

    Live hunts search Bazos, Aukro and Vinted for similar buy-now ads, store
    P25×0.75 under the product query in SQLite, and reuse that row on the next
    hunt while it is fresh. eBay is not a comps source. Offline fixtures still
    parse bundled eBay sold HTML so unit tests can check P25 math without the
    network.
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
        self._market_cache: dict[str, list[Listing]] = {}
        self._failed: dict[str, int] = {}
        self._asking_catalog: list[Listing] | None = None
        self.notes: list[str] = []
        self._noted: set[str] = set()
        self.misses: list[PriceBookMiss] = []
        self._miss_keys: set[str] = set()
        self._sold_html_blocked = False
        self._sold_html_status: int | None = None
        self._live_sold_used = 0
        self.live_sold_skipped = 0
        self._live_sold_budget = self.settings.comps_live_queries
        self._vinted = None
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
        """Use listings already fetched this hunt as extra price-book peers."""
        found: list[Listing] = []
        seen: set[str] = set()
        for item in listings:
            if item.marketplace.value in SITES and not item.is_immediate_buy():
                continue
            if item.price.amount <= 0:
                continue
            if not item.purchase_allowed(require_confirmation=item.marketplace.value in SITES):
                continue
            try:
                item = self._to_eur(item)
            except ValueError:
                continue
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
        """Conservative resale value from the SQLite price book."""
        full_text = listing_text(listing)
        subject = (subject or "").strip() or listing.title
        query = (query or "").strip() or sold_query(subject) or sold_query(full_text)
        if not query:
            return None
        min_n = int(rules()["hunt"]["min_sold_sample"])
        specs = specs if specs is not None else extract_specs(full_text)
        query = with_specs(query, specs)
        kind = classify_kind(full_text)
        self_key = _url_key(listing.url)

        cached = self._db_summary(query) or self._db_summary(f"ask:{query}")
        if cached and self._is_fresh(cached.fetched_at) and cached.n >= min_n:
            self._note(
                "price book: reused Bazos/Aukro/Vinted P25×0.75 from comps DB "
                f"({cached.query_key}, n={cached.n})"
            )
            return SoldComp(
                median=cached.median,
                sample=cached.n,
                label=_comp_label(cached.n, cached.source),
                reliable_for_buy=True,
            )

        if self._fixture_html is not None:
            return self._comp_from_fixture(subject, query, specs, kind, self_key, min_n)

        seed_peers = self._filter_peers(
            self._asking_catalog or [], subject, specs, kind, self_key
        )
        if len(seed_peers) >= min_n:
            return self._store_market_comp(query, seed_peers)

        # A heterogeneous newest-listings batch rarely contains five copies of
        # the same product. Fill those gaps with a bounded targeted search.
        peers = self._similar_market_peers(query, subject, specs, kind, self_key)
        if len(peers) >= min_n:
            return self._store_market_comp(query, peers)

        self._record_miss(listing, query=query, peers=peers, required=min_n)
        return None

    def _record_miss(
        self,
        listing: Listing,
        *,
        query: str,
        peers: list[Listing],
        required: int,
    ) -> None:
        key = _url_key(listing.url)
        if key in self._miss_keys:
            return
        self._miss_keys.add(key)
        typical = _market_value(peers) if peers else None
        self.misses.append(
            PriceBookMiss(
                listing=listing,
                query=query,
                peer_count=len(peers),
                required=required,
                typical=typical,
                peers=tuple(peers[:5]),
            )
        )

    def _store_market_comp(self, query: str, peers: list[Listing]) -> SoldComp:
        value = _market_value(peers)
        if self._db_path is not None:
            self._store_fetch(query, peers, peers, 200, _utc_now(), source="market")
        self._note(
            "price book: Bazos/Aukro/Vinted P25×0.75 stored in comps DB and reused "
            "(eBay is not used)"
        )
        return SoldComp(
            median=value,
            sample=len(peers),
            label=_comp_label(len(peers), "market"),
            reliable_for_buy=True,
        )

    def _filter_peers(
        self,
        listings: list[Listing],
        subject: str,
        specs: ItemSpecs | None,
        kind,
        self_key: str,
    ) -> list[Listing]:
        found: list[Listing] = []
        seen: set[str] = set()
        for item in listings:
            try:
                item = self._to_eur(item)
            except ValueError:
                continue
            if item.price.amount <= 0:
                continue
            if is_damaged_text(f"{item.title} {item.description}"):
                continue
            key = _url_key(item.url)
            if key == self_key or key in seen:
                continue
            if not similar_titles(subject, item.title, left_specs=specs, left_kind=kind):
                continue
            seen.add(key)
            found.append(item)
        return found

    def _comp_from_fixture(
        self,
        subject: str,
        query: str,
        specs: ItemSpecs | None,
        kind,
        self_key: str,
        min_n: int,
    ) -> SoldComp | None:
        hits, status, failed = self._sold_hits(query)
        if failed:
            return None
        sold = [
            item
            for item in hits
            if item.price.amount > 0
            and not is_damaged_text(f"{item.title} {item.description}")
            and _url_key(item.url) != self_key
        ]
        peers = [
            item
            for item in sold
            if similar_titles(subject, item.title, left_specs=specs, left_kind=kind)
        ]
        if self._db_path is not None:
            self._store_fetch(query, sold, peers, status, _utc_now(), source="ebay")
        if len(peers) < min_n:
            return None
        return SoldComp(
            median=_lower_quartile([item.price.amount for item in peers]),
            sample=len(peers),
            label=_comp_label(len(peers), "ebay"),
            reliable_for_buy=True,
        )

    def _similar_market_peers(
        self,
        query: str,
        subject: str,
        specs: ItemSpecs | None,
        kind,
        self_key: str,
    ) -> list[Listing]:
        return self._filter_peers(
            self._market_hits(query), subject, specs, kind, self_key
        )

    def _sold_hits(self, query: str) -> tuple[list[Listing], int | None, bool]:
        """Offline fixture path only. Live hunts never fetch eBay HTML."""
        if query in self._cache:
            return self._cache[query], self._failed.get(query), query in self._failed
        if self._fixture_html is None:
            return [], None, True
        hits = parse_ebay_html(self._fixture_html)
        self._cache[query] = hits
        return hits, 200, False

    def _market_hits(self, query: str) -> list[Listing]:
        if query in self._market_cache:
            return self._market_cache[query]
        seen: set[str] = set()
        hits: list[Listing] = []
        extra: list[Listing] = []
        if self._live_sold_used < self._live_sold_budget:
            self._live_sold_used += 1
            extra = self._live_market_search(query)
        else:
            self.live_sold_skipped += 1
            self._note(f"price book: live query budget exhausted ({self._live_sold_budget}); remaining products are unvalued")
        for item in (*extra, *(self._asking_catalog or ())):
            try:
                item = self._to_eur(item)
            except ValueError:
                continue
            key = _url_key(item.url)
            if key in seen:
                continue
            seen.add(key)
            hits.append(item)
        self._market_cache[query] = hits
        return hits

    def _live_market_search(self, query: str) -> list[Listing]:
        with ThreadPoolExecutor(max_workers=3) as pool:
            bazos = pool.submit(self._bazos_search, query)
            aukro = pool.submit(self._aukro_search, query)
            vinted = pool.submit(self._vinted_search, query)
            return [*bazos.result(), *aukro.result(), *vinted.result()]

    def _bazos_search(self, query: str) -> list[Listing]:
        url = f"{BAZOS_RSS['sk']}?{urlencode({'hledat': query})}"
        try:
            xml = self._get_text(url, accept="application/rss+xml")
        except httpx.HTTPError:
            return []
        return BazosRssClient(self.settings)._parse(xml, site="sk")

    def _aukro_search(self, query: str) -> list[Listing]:
        from bazar_deals.adapters.aukro import AukroHuntClient

        try:
            return AukroHuntClient(self.settings).search(query)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return []

    def _vinted_search(self, query: str) -> list[Listing]:
        from bazar_deals.adapters.vinted import VintedHuntClient, _BROWSER_UA

        if self._vinted is None:
            session = httpx.Client(
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
                },
                timeout=12.0,
                follow_redirects=True,
            )
            self._vinted = VintedHuntClient(self.settings, client=session)
        try:
            return self._vinted.search(query)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return []

    def _to_eur(self, item: Listing) -> Listing:
        from decimal import ROUND_FLOOR
        code = item.raw.get("original_price_currency", item.price.currency).upper()
        amount = item.price.to_eur(self.settings.eur_czk, eur_pln=self.settings.eur_pln)
        raw = dict(item.raw)
        if code in {"CZK", "PLN"} and not raw.get("fx_proceeds_adjusted"):
            amount = (amount * (1 - self.settings.fx_fee_rate)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
            raw["fx_proceeds_adjusted"] = True
        return item.model_copy(update={"price": item.price.model_copy(update={"amount": amount, "currency": "EUR"}), "raw": raw})

    def _get_text(self, url: str, *, accept: str) -> str:
        response = httpx.get(
            url,
            headers={
                "User-Agent": self.settings.bazos_user_agent,
                "Accept": accept,
            },
            timeout=12.0,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {response.status_code}")
        return response.text

    def _is_fresh(self, fetched_at: datetime) -> bool:
        ttl = timedelta(days=max(0, int(self.settings.comps_ttl_days)))
        return timedelta(0) <= _utc_now() - fetched_at <= ttl

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
                (_PRICE_BOOK_VERSION + query_key,),
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
                (_PRICE_BOOK_VERSION + query_key, n, str(value), _iso(fetched_at), source, status),
            )

    def _store_fetch(
        self,
        query_key: str,
        sold: list[Listing],
        peers: list[Listing],
        status: int | None,
        fetched_at: datetime,
        *,
        source: str = "market",
    ) -> None:
        if self._db_path is None:
            return
        stamp = _iso(fetched_at)
        if source == "market":
            value = _market_value(peers)
        else:
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
