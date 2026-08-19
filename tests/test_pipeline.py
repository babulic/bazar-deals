from pathlib import Path

from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.cli import main
from bazar_deals.domain import Action, Vertical
from bazar_deals.pipeline import hunt

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_rss.xml"


def test_rss_parses_price_from_title() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert listings[0].title == "Commodore 1541-II disk drive"
    assert listings[0].price.amount == 38


def test_hunt_flags_fixture_commodore() -> None:
    deals = hunt(BazosRssClient(fixture_path=FIXTURE), vertical=Vertical.RETRO)
    assert deals
    assert deals[0].action is Action.BUY
    assert deals[0].item.canonical_name == "Commodore 1541-II"


def test_cli_offline(capsys) -> None:
    assert main(["hunt", "--offline", "--vertical", "retro"]) == 0
    out = capsys.readouterr().out
    assert "Commodore 1541-II" in out
    assert "BUY" in out
