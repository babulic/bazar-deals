from pathlib import Path

from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.catalog import is_bulky
from bazar_deals.cli import main
from bazar_deals.domain import Action
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_rss.xml"
SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


def test_bazos_hunts_slovakia_only() -> None:
    assert BazosRssClient().sites == ("sk",)


def test_rss_parses_price_from_title() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert listings[0].title == "Commodore 1541-II disk drive"
    assert listings[0].price.amount == 38


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
    from decimal import Decimal

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

    from bazar_deals.config import Settings

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
