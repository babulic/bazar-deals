from decimal import Decimal
from pathlib import Path

from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.identity import ItemKind, classify_kind, similar_titles
from bazar_deals.soldcomps import SoldCompClient

ROOT = Path(__file__).parent / "fixtures"


def test_sold_quick_sale_price_uses_lower_quartile_not_median() -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1",
        title="Commodore 1541-II disk drive",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal("38"), currency="EUR"),
    )
    sold = SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html")
    comp = sold.median_sold(listing)
    assert comp is not None
    assert comp.sample == 6
    assert comp.median < Decimal("89.00")
    assert comp.reliable_for_buy is True
    assert "P25" in comp.label


def test_sold_computers_do_not_price_a_cassette() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="7089809337",
        title="Vzlámavanie Konami Commodore 64/128 C64 C128",
        url="https://aukro.sk/vzlamovanie-konami-commodore-64-128-c64-c128-7089809337",
        price=Money(amount=Decimal("12.62"), currency="EUR"),
    )
    sold = SoldCompClient(fixture_path=ROOT / "ebay_sold_c64_computers.html")
    assert sold.median_sold(listing) is None


def test_sold_computers_do_not_price_a_billardspiele_cassette() -> None:
    listing = Listing(
        marketplace=Marketplace.EBAY,
        external_id="287558443831",
        title="Computing Videothek Billardspiele Commodore 64/128",
        url="https://www.ebay.de/itm/287558443831",
        price=Money(amount=Decimal("24.40"), currency="EUR"),
    )
    sold = SoldCompClient(fixture_path=ROOT / "ebay_sold_c64_computers.html")
    assert classify_kind(listing.title) is ItemKind.MEDIA
    assert sold.median_sold(listing) is None


def test_phone_storage_must_match() -> None:
    assert not similar_titles(
        "Apple iPhone SE 2020 64 GB",
        "Apple iPhone SE 2020 256 GB",
    )
    assert similar_titles(
        "Apple iPhone SE 2020 64 GB",
        "Apple iPhone SE 2020 64GB schwarz",
    )


def test_phone_variant_must_match() -> None:
    assert not similar_titles("Apple iPhone 13 128 GB", "Apple iPhone 13 Pro 128 GB")
