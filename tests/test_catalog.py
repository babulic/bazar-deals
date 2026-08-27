from decimal import Decimal
from pathlib import Path

from bazar_deals.catalog import (
    is_bulky,
    is_skip_keyword,
    is_too_heavy,
    reject_physical,
    stated_weight_kg,
)
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.pipeline import score_listings
from bazar_deals.soldcomps import SoldCompClient

SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


def test_christmas_lights_and_string_lights_are_skipped() -> None:
    assert is_skip_keyword("Vánoční osvětlení 200 LED")
    assert is_skip_keyword("Vianočné osvetlenie 300 LED")
    assert is_skip_keyword("Světelný řetěz 200 LED")
    assert is_skip_keyword("Christmas lights 300 LED")
    assert not is_skip_keyword("Petzl čelovka Actik Core")
    assert reject_physical("Vánoční osvětlení 200 LED") == "skip_keyword"


def test_weight_cap_ignores_storage_and_allows_five_kg() -> None:
    assert stated_weight_kg("iPhone 16GB") is None
    assert not is_too_heavy("Hmotnosť 5 kg, posielam Packeta")
    assert is_too_heavy("Hmotnosť 6 kg")
    assert is_too_heavy("váha 5,5 kg")
    assert reject_physical("Starý gauč") == "bulky"
    assert is_bulky("Samsung televízor 55")


def test_sub_twenty_euro_listing_is_dropped_before_scoring() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="cheap-light",
        title="Petzl čelovka Actik Core",
        url="https://aukro.sk/inzerat/cheap-light",
        price=Money(amount=Decimal("13"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    assert run.deals == []
    assert run.funnel["under_min"] == 1


def test_christmas_lights_above_min_price_still_dropped() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="xmas",
        title="Vánoční osvětlení 200 LED",
        url="https://aukro.sk/inzerat/xmas",
        price=Money(amount=Decimal("25"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    assert run.deals == []
    assert run.funnel["skip_keyword"] == 1
