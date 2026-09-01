import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from bazar_deals.catalog import (
    hunt_fetch_queries,
    hunt_target_queries,
    is_fashion_drop,
    is_high_yield_kind,
    matches_hunt_target,
    skip_newest_dumps,
)
from bazar_deals.cli import main
from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.pipeline import HuntRun
from bazar_deals.research import (
    hunt_research_hint,
    retryable_sell_errors,
    sell_research_hint,
    should_research_loop,
    should_sell_research_loop,
    write_github_output,
)


@pytest.fixture(autouse=True)
def _isolate_hunt_research_env(monkeypatch) -> None:
    # setenv records an undo even when the var was unset. delenv(raising=False)
    # would no-op and let enable_hunt_research() leak into later test files.
    monkeypatch.setenv("BAZAR_HUNT_RESEARCH", "")
    monkeypatch.setenv("BAZAR_HUNT_EXPAND", "")


def test_target_sku_titles_are_prioritized() -> None:
    assert matches_hunt_target("Apple iPhone 13 128GB")
    assert matches_hunt_target("Commodore 1541")
    assert not matches_hunt_target("Dámske tričko veľkosť M")
    assert is_high_yield_kind("phones", "Apple iPhone 13 128GB")
    assert is_high_yield_kind("hardware", "Commodore 64 breadbin")
    assert is_high_yield_kind("hardware", "Apple Watch SE")
    assert not is_high_yield_kind("media", "Computing Videothek Billardspiele Commodore 64/128")
    assert not is_high_yield_kind("accessories", "pasek Apple Watch Alpine Loop")
    assert not is_high_yield_kind("clothing", "Nike tričko")


def test_expand_queries_join_only_in_research_mode(monkeypatch) -> None:
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    monkeypatch.delenv("BAZAR_HUNT_EXPAND", raising=False)
    base = hunt_target_queries()
    assert "iphone" in base
    assert "pixel" in base
    assert "iphone 14" not in base
    monkeypatch.setenv("BAZAR_HUNT_RESEARCH", "1")
    expanded = hunt_target_queries()
    assert "iphone" in expanded
    assert "iphone 14" in expanded
    assert "nintendo 3ds" in expanded
    assert "vltavín" in expanded


def test_fetch_queries_search_buyable_skus_not_cassette_keywords(monkeypatch) -> None:
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    monkeypatch.delenv("BAZAR_HUNT_EXPAND", raising=False)
    fetch = hunt_fetch_queries()
    assert "iphone se" in fetch
    assert "commodore 1541" in fetch
    assert "commodore 64 computer" in fetch
    assert "nintendo switch lite" in fetch
    assert "apple watch se" in fetch
    assert "galaxy s21" in fetch
    assert "iphone 11" in fetch
    assert "c64" not in fetch
    assert "commodore" not in fetch
    assert "pokemon" not in fetch
    assert "funko pop" not in fetch


def test_fashion_drop_hits_tees_not_iphones_or_watches() -> None:
    assert is_fashion_drop("Dámske tričko veľkosť M")
    assert is_fashion_drop("Nike hoodie black")
    assert is_fashion_drop("Kabelka Michael Kors")
    assert not is_fashion_drop("Apple iPhone 13 128GB")
    assert not is_fashion_drop("Apple Watch SE GPS 40mm")
    assert not is_fashion_drop("Facebook Marketplace inzerát")


def test_zero_buy_hint_points_at_more_hits_not_tighter_gates() -> None:
    hint = hunt_research_hint(Counter(usable=80, no_sold_comps=71, buy=0))
    assert "expand" in hint.casefold() or "comps" in hint
    empty = hunt_research_hint(Counter(usable=0, buy=0))
    assert "widen" in empty
    assert "kúpim" in sell_research_hint(buyers=0, fetched=12)
    assert "boards" in sell_research_hint(buyers=0, fetched=0)


def test_github_output_writes_buys_and_buyers(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    write_github_output(buys=0, buyers=0, research=1, looped=1)
    text = target.read_text(encoding="utf-8")
    assert "buys=0" in text
    assert "buyers=0" in text
    assert "research=1" in text
    assert "looped=1" in text


def test_should_research_loop_only_after_zero_buy_live_hunt() -> None:
    assert should_research_loop(buy_count=0, already_research=False, offline=False)
    assert not should_research_loop(buy_count=1, already_research=False, offline=False)
    assert not should_research_loop(buy_count=0, already_research=True, offline=False)
    assert not should_research_loop(buy_count=0, already_research=False, offline=True)


def test_should_sell_research_loop_on_zero_buyers_or_ebay_429() -> None:
    assert should_sell_research_loop(
        buyers=0, notes=[], already_research=False, offline=False
    )
    assert should_sell_research_loop(
        buyers=1,
        notes=["ebay: HTTP 429 after retries — remaining eBay searches stopped"],
        already_research=False,
        offline=False,
    )
    assert not should_sell_research_loop(
        buyers=0, notes=[], already_research=True, offline=False
    )
    assert not should_sell_research_loop(
        buyers=0, notes=[], already_research=False, offline=True
    )
    walls = [
        "facebook: skipped (public marketplace is a login wall)",
        "olx.pl: skipped (public search is a login wall)",
    ]
    assert retryable_sell_errors(walls) == []
    assert not should_sell_research_loop(
        buyers=1, notes=walls, already_research=False, offline=False
    )


def test_sku_search_skips_vinted_and_aukro_newest_dumps(monkeypatch) -> None:
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    assert skip_newest_dumps()
    monkeypatch.setattr("bazar_deals.catalog.hunt_fetch_queries", lambda: ())
    assert not skip_newest_dumps()
    monkeypatch.setenv("BAZAR_HUNT_RESEARCH", "1")
    assert skip_newest_dumps()


def _cached_listing(path: Path) -> Path:
    path.write_text(
        json.dumps(
            [
                {
                    "marketplace": "bazos",
                    "external_id": "1",
                    "title": "Apple iPhone SE",
                    "url": "https://mobil.bazos.sk/inzerat/1/",
                    "price": {"amount": "40", "currency": "EUR"},
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_zero_buy_runs_in_process_research_loop(monkeypatch, tmp_path: Path) -> None:
    cached = _cached_listing(tmp_path / "ads.json")
    calls: list[tuple] = []

    def fake_score(listings, *args, **kwargs):
        calls.append(("score", len(listings)))
        return HuntRun(
            deals=[],
            funnel=Counter(usable=len(listings)),
            source_stats={},
            listings=list(listings),
        )

    def fake_hunt_sources(*args, **kwargs):
        assert kwargs.get("score") is False
        calls.append(("fetch",))
        extra = Listing(
            marketplace=Marketplace.BAZOS,
            external_id="2",
            title="Commodore 1541-II",
            url="https://pc.bazos.sk/inzerat/2/",
            price=Money(amount=Decimal("38"), currency="EUR"),
        )
        return HuntRun(
            deals=[],
            funnel=Counter(),
            source_stats={},
            listings=[extra],
            fetch_notes=["expanded"],
        )

    monkeypatch.setattr("bazar_deals.cli.score_listings", fake_score)
    monkeypatch.setattr("bazar_deals.cli.hunt_sources", fake_hunt_sources)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    monkeypatch.delenv("BAZAR_HUNT_EXPAND", raising=False)
    assert main(["hunt", "--listings-in", str(cached)]) == 0
    assert calls[0] == ("score", 1)
    assert calls[1] == ("fetch",)
    assert calls[2] == ("score", 2)


def test_research_flag_does_not_recurse_the_zero_buy_loop(monkeypatch, tmp_path: Path) -> None:
    cached = _cached_listing(tmp_path / "ads.json")
    calls: list[str] = []

    def fake_score(listings, *args, **kwargs):
        calls.append("score")
        return HuntRun(
            deals=[],
            funnel=Counter(usable=len(listings)),
            source_stats={},
            listings=list(listings),
        )

    def fake_hunt_sources(*args, **kwargs):
        calls.append("fetch")
        return HuntRun(deals=[], funnel=Counter(), source_stats={}, listings=[], fetch_notes=[])

    monkeypatch.setattr("bazar_deals.cli.score_listings", fake_score)
    monkeypatch.setattr("bazar_deals.cli.hunt_sources", fake_hunt_sources)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )
    assert main(["hunt", "--research", "--listings-in", str(cached)]) == 0
    assert calls == ["fetch", "score"]


def test_offline_zero_buy_does_not_research_loop(monkeypatch, tmp_path: Path) -> None:
    cached = _cached_listing(tmp_path / "ads.json")
    calls: list[str] = []

    def fake_score(listings, *args, **kwargs):
        calls.append("score")
        return HuntRun(
            deals=[],
            funnel=Counter(usable=len(listings)),
            source_stats={},
            listings=list(listings),
        )

    def fake_hunt_sources(*args, **kwargs):
        calls.append("fetch")
        return HuntRun(deals=[], funnel=Counter(), source_stats={}, listings=[])

    monkeypatch.setattr("bazar_deals.cli.score_listings", fake_score)
    monkeypatch.setattr("bazar_deals.cli.hunt_sources", fake_hunt_sources)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )
    assert main(["hunt", "--offline", "--listings-in", str(cached)]) == 0
    assert calls == ["score"]
