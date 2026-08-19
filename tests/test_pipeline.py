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


def test_hunt_flags_fixture_commodore() -> None:
    deals = hunt(BazosRssClient(fixture_path=FIXTURE), sold=SoldCompClient(fixture_path=SOLD))
    cheap = [deal for deal in deals if deal.item.listing.price.amount == 38]
    assert cheap
    assert cheap[0].action is Action.BUY
    assert cheap[0].item.canonical_name == "Commodore 1541-II disk drive"


def test_fixture_drops_bulky_couch() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert all("gauč" not in item.title.casefold() for item in listings)
    assert is_bulky("Starý gauč")


def test_cli_offline(capsys) -> None:
    assert main(["hunt", "--offline", "--source", "bazos"]) == 0
    out = capsys.readouterr().out
    assert "Commodore 1541-II" in out
    assert "BUY" in out
    assert "obvyklá" in out
    assert "filter:" in out
    assert "buy=" in out


def test_hunt_alerts_when_sold_comps_missing(tmp_path) -> None:
    from unittest.mock import patch

    from bazar_deals.config import Settings

    class _Resp:
        status_code = 403
        text = "blocked"
        url = "https://www.ebay.de/sch/i.html"

    settings = Settings(comps_db=str(tmp_path / "comps.sqlite"), max_no_comp_alerts=5)
    sold = SoldCompClient(settings)
    with patch("bazar_deals.soldcomps.httpx.get", return_value=_Resp()):
        deals = hunt(BazosRssClient(fixture_path=FIXTURE), settings=settings, sold=sold)
    alerts = [deal for deal in deals if deal.action is Action.ALERT]
    assert alerts
    assert all(deal.action is not Action.BUY for deal in deals)
    assert alerts[0].costs.estimated_resale == 0
