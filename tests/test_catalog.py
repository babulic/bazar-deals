from decimal import Decimal
from pathlib import Path

from bazar_deals.catalog import (
    is_bulky,
    is_christmas_lighting,
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


def test_christmas_lights_are_skipped_headlamps_and_lamps_are_not() -> None:
    assert is_christmas_lighting("Vánoční osvětlení 200 LED")
    assert is_christmas_lighting("Vianočné osvetlenie 300 LED")
    assert is_christmas_lighting("Vianočné svetlá na stromček")
    assert is_christmas_lighting("Christmas lights 300 LED")
    assert is_christmas_lighting("Světelný řetěz 200 LED")
    assert is_skip_keyword("Weihnachtsbeleuchtung 200 LED")
    assert not is_christmas_lighting("Petzl čelovka Actik Core")
    assert not is_christmas_lighting("Čelovka 1000 lm LED")
    assert not is_christmas_lighting("LED osvetlenie na bicykel")
    assert not is_christmas_lighting("Stolná lampa Philips")
    assert not is_christmas_lighting("Nabíjačka a osvetlenie stanu")
    assert reject_physical("Vánoční osvětlení 200 LED") == "skip_keyword"
    assert reject_physical("Petzl čelovka Actik Core") is None


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


def test_headlamp_above_min_price_is_not_treated_as_christmas_lights() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="headlamp",
        title="Petzl čelovka Actik Core",
        url="https://aukro.sk/inzerat/headlamp",
        price=Money(amount=Decimal("45"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    assert run.funnel["skip_keyword"] == 0
