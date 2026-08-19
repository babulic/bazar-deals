from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Listing
from bazar_deals.htmlparse import parse_ebay_html
from bazar_deals.identity import similar_titles, sold_query
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


def _comp_label(n: int) -> str:
    return f"obvyklá cena, funkčný kus, ebay.de sold (n={n})"


class SoldCompClient:
    """Median of recent eBay.de *sold* prices for a tight query.

    Hunt reads SQLite first. eBay sold HTML is fetched only to fill a miss,
    a stale row, or a sample below min_sold_sample. A 403/fail falls back to
    the last stored median when one exists — never invent a price.

    Default path is public sold-search HTML (`LH_Sold=1&LH_Complete=1`).
    Keepa/Terapeak need paid keys — optional hook only, never required.
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
        self._fixture_html = fixture_html
        if fixture_path is not None:
            self._fixture_html = fixture_path.read_text(encoding="utf-8")
        if self._fixture_html is not None:
            self._db_path: Path | None = None
        else:
            self._db_path = Path(db_path) if db_path is not None else Path(self.settings.comps_db)
            self._init_db()

    def median_sold(self, listing: Listing) -> SoldComp | None:
        query = sold_query(f"{listing.title} {listing.description}")
        if not query:
            return None
        min_n = int(rules()["hunt"]["min_sold_sample"])
        hay = f"{listing.title} {listing.description}"
        summary = self._db_summary(query)
        if summary and self._is_fresh(summary.fetched_at) and summary.n >= min_n:
            return SoldComp(median=summary.median, sample=summary.n, label=_comp_label(summary.n))

        hits, status, failed = self._sold_hits(query)
        if failed:
            if summary and summary.n >= min_n:
                return SoldComp(median=summary.median, sample=summary.n, label=_comp_label(summary.n))
            return None

        sold = [
            item
            for item in hits
            if item.price.amount > 0 and not is_damaged_text(item.title)
        ]
        peers = [item for item in sold if similar_titles(hay, item.title)]
        fetched_at = _utc_now()
        if self._db_path is not None and not failed:
            self._store_fetch(query, sold, peers, status, fetched_at)

        if len(peers) < min_n:
            return None
        n = len(peers)
        return SoldComp(median=_median([item.price.amount for item in peers]), sample=n, label=_comp_label(n))

    def _sold_hits(self, query: str) -> tuple[list[Listing], int | None, bool]:
        if query in self._cache:
            return self._cache[query], self._failed.get(query), query in self._failed
        if query in self._failed:
            return [], self._failed[query], True
        if self._fixture_html is not None:
            hits = parse_ebay_html(self._fixture_html)
            self._cache[query] = hits
            return hits, 200, False
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
        status = int(response.status_code)
        if status >= 400 or "signin.ebay." in str(response.url):
            self._failed[query] = status
            return [], status, True
        hits = parse_ebay_html(response.text)
        self._cache[query] = hits
        return hits, status, False

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

    def _store_fetch(
        self,
        query_key: str,
        sold: list[Listing],
        peers: list[Listing],
        status: int | None,
        fetched_at: datetime,
    ) -> None:
        if self._db_path is None:
            return
        stamp = _iso(fetched_at)
        median = _median([item.price.amount for item in peers]) if peers else Decimal("0")
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
                conn.execute(
                    "INSERT INTO sold_queries (query_key, n, median_eur, fetched_at, source, http_status) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(query_key) DO UPDATE SET "
                    "n = excluded.n, median_eur = excluded.median_eur, fetched_at = excluded.fetched_at, "
                    "source = excluded.source, http_status = excluded.http_status",
                    (query_key, len(peers), str(median), stamp, "ebay", status),
                )
            else:
                existing = conn.execute(
                    "SELECT n FROM sold_queries WHERE query_key = ?",
                    (query_key,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO sold_queries (query_key, n, median_eur, fetched_at, source, http_status) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (query_key, 0, "0", stamp, "ebay", status),
                    )
                else:
                    conn.execute(
                        "UPDATE sold_queries SET http_status = ? WHERE query_key = ?",
                        (status, query_key),
                    )
