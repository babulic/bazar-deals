from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.soldcomps import SoldCompClient, _lower_quartile, _market_value

ROOT = Path(__file__).parent / "fixtures"
SOLD_HTML = (ROOT / "ebay_sold_1541.html").read_text(encoding="utf-8")


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


def _peers(n: int = 6) -> list[Listing]:
    prices = [Decimal("80"), Decimal("85"), Decimal("90"), Decimal("95"), Decimal("100"), Decimal("110")]
    return [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=f"peer-{index}",
            title="Commodore 1541-II disk drive",
            url=f"https://pc.bazos.sk/inzerat/peer-{index}/",
            price=Money(amount=prices[index], currency="EUR"),
        )
        for index in range(n)
    ]


def test_cache_hit_skips_network(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    peers = _peers()
    first_client = SoldCompClient(_settings(db))
    with (
        patch.object(first_client, "_bazos_search", return_value=peers) as bazos,
        patch.object(first_client, "_aukro_search", return_value=[]) as aukro,
        patch.object(first_client, "_vinted_search", return_value=[]) as vinted,
    ):
        first = first_client.median_sold(listing)
    assert first is not None
    assert bazos.call_count == 1
    assert aukro.call_count == 1
    assert vinted.call_count == 1
    second_client = SoldCompClient(_settings(db))
    with (
        patch.object(second_client, "_bazos_search", side_effect=AssertionError("network")) as bazos,
        patch.object(second_client, "_aukro_search", side_effect=AssertionError("network")),
        patch.object(second_client, "_vinted_search", side_effect=AssertionError("network")),
    ):
        second = second_client.median_sold(listing)
    assert second is not None
    assert second.median == first.median
    assert second.sample == first.sample
    assert second.reliable_for_buy is True
    bazos.assert_not_called()


def test_cache_miss_fetches_conservative_p25(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    peers = _peers()
    client = SoldCompClient(_settings(db))
    with (
        patch.object(client, "_bazos_search", return_value=peers),
        patch.object(client, "_aukro_search", return_value=[]),
        patch.object(client, "_vinted_search", return_value=[]),
    ):
        comp = client.median_sold(_listing())
    assert comp is not None
    assert comp.sample == 6
    assert comp.median == _market_value(peers)
    assert comp.median == Decimal("63.75")
    assert comp.reliable_for_buy is True
    import sqlite3

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT n, median_eur, source FROM sold_queries").fetchone()
    assert row is not None
    assert int(row[0]) == 6
    assert Decimal(row[1]) == Decimal("63.75")
    assert row[2] == "market"


def test_failed_refresh_does_not_use_stale_price_for_buy(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    peers = _peers()
    first = SoldCompClient(_settings(db, ttl=7))
    with (
        patch.object(first, "_bazos_search", return_value=peers),
        patch.object(first, "_aukro_search", return_value=[]),
        patch.object(first, "_vinted_search", return_value=[]),
    ):
        stored = first.median_sold(listing)
    assert stored is not None
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE sold_queries SET fetched_at = ?", (stale,))
    second = SoldCompClient(_settings(db, ttl=7))
    with (
        patch.object(second, "_bazos_search", return_value=[]) as bazos,
        patch.object(second, "_aukro_search", return_value=[]),
        patch.object(second, "_vinted_search", return_value=[]),
    ):
        fallback = second.median_sold(listing)
    assert fallback is None
    bazos.assert_called_once()


def test_ttl_expiry_refetches(tmp_path: Path) -> None:
    db = tmp_path / "bazar-comps.sqlite"
    listing = _listing()
    peers = _peers()
    first = SoldCompClient(_settings(db, ttl=7))
    with (
        patch.object(first, "_bazos_search", return_value=peers),
        patch.object(first, "_aukro_search", return_value=[]),
        patch.object(first, "_vinted_search", return_value=[]),
    ):
        first.median_sold(listing)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE sold_queries SET fetched_at = ?", (stale,))
    second = SoldCompClient(_settings(db, ttl=7))
    with (
        patch.object(second, "_bazos_search", return_value=peers) as bazos,
        patch.object(second, "_aukro_search", return_value=[]),
        patch.object(second, "_vinted_search", return_value=[]),
    ):
        refreshed = second.median_sold(listing)
    assert refreshed is not None
    bazos.assert_called_once()


def test_empty_db_file_is_created(tmp_path: Path) -> None:
    db = tmp_path / "cache" / "bazar-comps.sqlite"
    SoldCompClient(_settings(db))
    assert db.is_file()
    assert db.stat().st_size > 0


def test_long_marketplace_descriptions_do_not_hide_exact_title_match(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    peers = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title=f"Apple iPhone 13 128GB color {index}",
            description=" ".join(f"unrelated{word}" for word in range(40)),
            url=f"https://mobil.bazos.sk/inzerat/{index}/",
            price=Money(amount=Decimal(200 + index), currency="EUR"),
        )
        for index in range(5)
    ]
    listing = Listing(
        marketplace=Marketplace.VINTED,
        external_id="candidate",
        title="Apple iPhone 13 128GB",
        description="Plne funkčný telefón s dlhou detailnou informáciou.",
        url="https://www.vinted.sk/items/candidate",
        price=Money(amount=Decimal("80"), currency="EUR"),
    )
    with patch.object(client, "_market_hits", return_value=peers):
        comp = client.median_sold(listing)
    assert comp is not None
    assert comp.reliable_for_buy is True
    assert comp.sample == 5
    assert comp.median == _market_value(peers)
    assert _lower_quartile([item.price.amount for item in peers]) == Decimal("201.00")
    assert comp.median == Decimal("150.75")


def test_sold_lookup_key_includes_capacity_from_the_body(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    seen: list[str] = []

    def fake_hits(query: str):
        seen.append(query)
        return []

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="iphone-body",
        title="Apple iPhone 13",
        description="Kapacita 128 GB, plne funkčný.",
        url="https://mobil.bazos.sk/inzerat/iphone-body/",
        price=Money(amount=Decimal("80"), currency="EUR"),
    )
    with patch.object(client, "_market_hits", side_effect=fake_hits):
        client.median_sold(listing)
    assert seen
    assert "128gb" in seen[0]


def test_fixture_html_still_uses_ebay_p25() -> None:
    client = SoldCompClient(fixture_html=SOLD_HTML)
    comp = client.median_sold(_listing())
    assert comp is not None
    assert comp.sample == 6
    assert comp.median == Decimal("85.00")
    assert comp.reliable_for_buy is True
    assert "ebay.de sold P25" in comp.label


def test_unique_queries_share_one_live_price_book_search(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    peers = _peers()
    with (
        patch.object(client, "_bazos_search", return_value=peers) as bazos,
        patch.object(client, "_aukro_search", return_value=[]),
        patch.object(client, "_vinted_search", return_value=[]),
    ):
        first = client.median_sold(_listing())
        second = client.median_sold(
            _listing().model_copy(
                update={
                    "external_id": "2",
                    "url": "https://pc.bazos.sk/inzerat/1541-other/",
                }
            )
        )
    assert first is not None and second is not None
    assert bazos.call_count == 1
    assert first.median == second.median


def test_hunt_batch_seed_skips_live_marketplace_search(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    peers = _peers()
    client.seed_asking(peers)
    with (
        patch.object(client, "_bazos_search", side_effect=AssertionError("live")) as bazos,
        patch.object(client, "_aukro_search", side_effect=AssertionError("live")),
        patch.object(client, "_vinted_search", side_effect=AssertionError("live")),
    ):
        comp = client.median_sold(_listing())
    assert comp is not None
    assert comp.reliable_for_buy is True
    assert comp.sample == 6
    assert comp.median == _market_value(peers)
    bazos.assert_not_called()


def test_thin_hunt_batch_searches_for_missing_comparables(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    client.seed_asking(_peers(3))
    with (
        patch.object(client, "_bazos_search", return_value=_peers()) as bazos,
        patch.object(client, "_aukro_search", return_value=[]),
        patch.object(client, "_vinted_search", return_value=[]),
    ):
        assert client.median_sold(_listing()) is not None
    bazos.assert_called_once()


def test_targeted_search_respects_budget(tmp_path: Path) -> None:
    client = SoldCompClient(Settings(comps_db=str(tmp_path / "comps.sqlite"), comps_live_queries=1))
    client.seed_asking([])
    with patch.object(client, "_live_market_search", return_value=[]) as search:
        client.median_sold(_listing(), query="Commodore 1541")
        client.median_sold(_listing(), query="Commodore 1571")
    assert search.call_count == 1
    assert client.live_sold_skipped == 1


def test_unversioned_price_book_cannot_bypass_product_role_checks(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    with client._connect() as db:
        db.execute("INSERT INTO sold_queries VALUES (?,?,?,?,?,?)",
                   ("Nintendo Switch Lite", 75, "75.14", datetime.now(timezone.utc).isoformat(), "market", 200))
    assert client._db_summary("Nintendo Switch Lite") is None


def test_insufficient_comps_record_listing_link_and_thin_typical(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    thin = _peers(2)
    with patch.object(client, "_live_market_search", return_value=thin):
        assert client.median_sold(_listing()) is None
    assert client.misses
    miss = client.misses[0]
    assert str(miss.listing.url) == "https://pc.bazos.sk/inzerat/1541/"
    assert miss.listing.price.amount == Decimal("38")
    assert miss.peer_count == 2
    assert miss.required == 5
    assert miss.typical == _market_value(thin)
    assert miss.peers[0].url == thin[0].url
    assert not any("insufficient comparable ads" in note for note in client.notes)


def test_zero_peer_miss_has_no_usual_price(tmp_path: Path) -> None:
    client = SoldCompClient(_settings(tmp_path / "comps.sqlite"))
    with patch.object(client, "_live_market_search", return_value=[]):
        assert client.median_sold(_listing()) is None
    miss = client.misses[0]
    assert miss.peer_count == 0
    assert miss.typical is None
    assert miss.peers == ()
