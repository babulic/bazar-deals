from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.soldcomps import SoldCompClient

ROOT = Path(__file__).parent / "fixtures"
SOLD_HTML = (ROOT / "ebay_sold_1541.html").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, status: int, text: str = "", url: str = "https://www.ebay.de/sch/i.html") -> None:
        self.status_code = status
        self.text = text
        self.url = url


def _listing() -> Listing:
    return Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1",
        title="Commodore 1541-II disk drive",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal("38"), currency="EUR"),
    )


def _settings(db: Path, ttl: int = 7) -> Settings:
    return Settings(comps_db=str(db), comps_ttl_days=ttl)


def test_cache_hit_skips_network(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(200, SOLD_HTML)) as fetch:
        first = SoldCompClient(_settings(db)).median_sold(listing)
    assert first is not None
    assert fetch.call_count == 1
    with patch("bazar_deals.soldcomps.httpx.get", side_effect=AssertionError("network")) as fetch:
        second = SoldCompClient(_settings(db)).median_sold(listing)
    assert second is not None
    assert second.median == first.median
    assert second.sample == first.sample
    fetch.assert_not_called()


def test_cache_miss_fetches(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(200, SOLD_HTML)) as fetch:
        comp = SoldCompClient(_settings(db)).median_sold(_listing())
    assert comp is not None
    assert comp.sample == 6
    assert comp.median == Decimal("89.00")
    fetch.assert_called_once()


def test_forbidden_uses_stale_db(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(200, SOLD_HTML)):
        stored = SoldCompClient(_settings(db, ttl=7)).median_sold(listing)
    assert stored is not None
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE sold_queries SET fetched_at = ?", (stale,))
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(403, "blocked")) as fetch:
        fallback = SoldCompClient(_settings(db, ttl=7)).median_sold(listing)
    assert fallback is not None
    assert fallback.median == stored.median
    fetch.assert_called_once()


def test_ttl_expiry_refetches(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(200, SOLD_HTML)):
        SoldCompClient(_settings(db, ttl=7)).median_sold(listing)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE sold_queries SET fetched_at = ?", (stale,))
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp(200, SOLD_HTML)) as fetch:
        SoldCompClient(_settings(db, ttl=7)).median_sold(listing)
    fetch.assert_called_once()


def test_empty_db_file_is_created(tmp_path: Path) -> None:
    db = tmp_path / "cache" / "bazar-comps.sqlite"
    SoldCompClient(_settings(db))
    assert db.is_file()
    assert db.stat().st_size > 0
