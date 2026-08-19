from decimal import Decimal
from pathlib import Path

from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.soldcomps import SoldCompClient

ROOT = Path(__file__).parent / "fixtures"


def test_sold_median_from_ebay_html() -> None:
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
    assert comp.median == Decimal("89.00")
    assert "ebay.de" in comp.label


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
