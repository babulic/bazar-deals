from pathlib import Path

import pytest

from bazar_deals.adapters.aukro import AukroHuntClient
from bazar_deals.adapters.vinted import VintedHuntClient, VintedProClient
from bazar_deals.domain import Marketplace, Vertical
from bazar_deals.htmlparse import parse_bazos_detail, parse_json_ld_products, parse_vinted_detail

ROOT = Path(__file__).parent / "fixtures"


def test_aukro_json_ld_fixture() -> None:
    listings = AukroHuntClient(fixture_path=ROOT / "aukro.html").fetch_new()
    assert listings[0].title == "iPhone 13"
    assert listings[0].price.amount == 80
    assert listings[0].marketplace is Marketplace.AUKRO


def test_json_ld_description_is_kept_for_scoring() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"iPhone SE 2020 64 GB",'
        '"description":"Batéria 77 %, bez krabičky.",'
        '"url":"https://aukro.sk/iphone-se-1",'
        '"offers":{"@type":"Offer","price":"50","priceCurrency":"EUR"}}'
        "</script>"
    )
    listing = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")[0]
    assert "77 %" in listing.description
    assert "bez krabičky" in listing.description


def test_vinted_html_fixture() -> None:
    listings = VintedHuntClient(fixture_path=ROOT / "vinted.html").fetch_new()
    assert listings[0].external_id == "4242"
    assert listings[0].price.amount == 90


def test_vinted_detail_extracts_description() -> None:
    html = '{"description":"Batéria 77 %, bez krabičky","status":"Veľmi dobrý"}'
    detail = parse_vinted_detail(html)
    assert "77 %" in detail
    assert "bez krabičky" in detail
    assert "Veľmi dobrý" in detail


def test_bazos_detail_extracts_meta_description() -> None:
    html = '<meta name="description" content="iPhone SE 2020, batéria 77 %, bez krabice">'
    detail = parse_bazos_detail(html)
    assert "77 %" in detail
    assert "bez krabice" in detail


def test_vinted_pro_still_sell_side() -> None:
    with pytest.raises(RuntimeError, match="sell-side"):
        VintedProClient().fetch_new(Vertical.APPLE)
