from decimal import Decimal
from pathlib import Path

from bazar_deals.adapters.bazos import BazosRssClient, _split_price
from bazar_deals.catalog import is_bulky
from bazar_deals.cli import main
from bazar_deals.config import Settings
from bazar_deals.domain import AIReview, Action, Listing, Marketplace, Money
from bazar_deals.pipeline import hunt, score_listings
from bazar_deals.soldcomps import SoldComp, SoldCompClient

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_rss.xml"
SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


def test_bazos_hunts_slovakia_only() -> None:
    assert BazosRssClient().sites == ("sk",)


def test_rss_parses_price_from_title() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert listings[0].title == "Commodore 1541-II disk drive"
    assert listings[0].price.amount == 38


def test_current_bazos_rss_colon_price_is_parsed() -> None:
    amount, title = _split_price("Apple iPhone 13 128 GB: 1 099")
    assert title == "Apple iPhone 13 128 GB"
    assert amount == 1099


def test_unpriced_bazos_suffix_remains_zero() -> None:
    amount, title = _split_price("Apple iPhone 13: Dohodou")
    assert title == "Apple iPhone 13"
    assert amount == 0


def test_fixture_is_scored_but_not_buy_when_net_profit_is_under_30() -> None:
    deals = hunt(BazosRssClient(fixture_path=FIXTURE), sold=SoldCompClient(fixture_path=SOLD))
    cheap = [deal for deal in deals if deal.item.listing.price.amount == 38]
    assert cheap
    assert cheap[0].action is Action.SKIP
    assert cheap[0].costs.net_profit < 30
    assert cheap[0].item.canonical_name == "Commodore 1541-II disk drive"


def test_fixture_drops_bulky_couch() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert all("gauč" not in item.title.casefold() for item in listings)
    assert is_bulky("Starý gauč")


def test_cli_offline_reports_no_false_buy(capsys) -> None:
    assert main(["hunt", "--offline", "--source", "bazos"]) == 0
    out = capsys.readouterr().out
    assert "filter:" in out
    assert "No deals" in out


def test_under_min_price_is_dropped() -> None:
    from bazar_deals.adapters.base import ListingSource
    from bazar_deals.domain import Listing, Marketplace, Money, Vertical

    class _Cheap(ListingSource):
        marketplace = Marketplace.BAZOS.value

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return [
                Listing(
                    marketplace=Marketplace.BAZOS,
                    external_id="lv",
                    title="LOUIS VUITTON BLACK EDITION",
                    url="https://oblecenie.bazos.sk/inzerat/lv/",
                    price=Money(amount=Decimal("0.15"), currency="EUR"),
                )
            ]

    deals = hunt(_Cheap(), sold=SoldCompClient(fixture_path=SOLD))
    assert deals == []


def test_asking_only_market_comps_cannot_create_buy(tmp_path) -> None:
    from unittest.mock import patch

    class _Resp:
        def __init__(self, status: int, text: str = "", url: str = "https://example.com") -> None:
            self.status_code = status
            self.text = text
            self.url = url

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    rss = FIXTURE.read_text(encoding="utf-8")

    def fake_get(url, **kwargs):
        target = str(url)
        if "ebay.de" in target:
            return _Resp(403, "blocked", target)
        if "bazos" in target:
            return _Resp(200, rss, target)
        return _Resp(200, "<html></html>", target)

    settings = Settings(comps_db=str(tmp_path / "comps.sqlite"))
    sold = SoldCompClient(settings)
    with patch("bazar_deals.soldcomps.httpx.get", side_effect=fake_get):
        deals = hunt(BazosRssClient(fixture_path=FIXTURE), settings=settings, sold=sold)
    assert deals == []


def test_invalid_price_has_its_own_funnel_metric(capsys) -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="zero",
        title="Apple iPhone 13 128 GB",
        url="https://mobil.bazos.sk/inzerat/zero/",
        price=Money(amount=0, currency="EUR"),
    )
    score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    out = capsys.readouterr().out
    assert "not_buy_now=0" in out
    assert "invalid_price=1" in out


def test_asking_only_comp_can_reach_required_ai_gate() -> None:
    class _AskingComps:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="asking-only conservative P25 with haircut (n=8)",
                reliable_for_buy=False,
            )

    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("110"),
                confidence=0.9,
                reason="Exact model and conservative quick-sale value verified.",
                source_urls=["https://example.com/evidence"],
                model="copilot:auto",
            )

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="iphone",
        title="Apple iPhone 13 128GB",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
        url="https://mobil.bazos.sk/inzerat/iphone/",
        price=Money(amount=20, currency="EUR"),
    )
    settings = Settings(ai_review_enabled=True, ai_review_required=True, min_net_profit_eur=30)
    deals = score_listings([listing], settings, _AskingComps(), reviewer=_Reviewer()).deals
    assert len(deals) == 1
    assert deals[0].action is Action.BUY
    assert deals[0].ai_review is not None


def test_lookup_budget_counts_unique_queries(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_sold_lookups"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="sold P25 (n=8)",
                reliable_for_buy=True,
            )

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/{index}/",
            price=Money(amount=20 + index, currency="EUR"),
        )
        for index in range(2)
    ]
    deals = score_listings(listings, Settings(), _Sold()).deals
    assert len(deals) == 2
