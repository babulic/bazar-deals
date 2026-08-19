from pathlib import Path

import pytest

from bazar_deals.adapters.aukro import AukroHuntClient
from bazar_deals.adapters.vinted import VintedHuntClient, VintedProClient
from bazar_deals.domain import Marketplace, Vertical

ROOT = Path(__file__).parent / "fixtures"


def test_aukro_json_ld_fixture() -> None:
    listings = AukroHuntClient(fixture_path=ROOT / "aukro.html").fetch_new()
    assert listings[0].title == "iPhone 13"
    assert listings[0].price.amount == 80
    assert listings[0].marketplace is Marketplace.AUKRO


def test_vinted_html_fixture() -> None:
    listings = VintedHuntClient(fixture_path=ROOT / "vinted.html").fetch_new()
    assert listings[0].external_id == "4242"
    assert listings[0].price.amount == 90


def test_vinted_pro_still_sell_side() -> None:
    with pytest.raises(RuntimeError, match="sell-side"):
        VintedProClient().fetch_new(Vertical.APPLE)
