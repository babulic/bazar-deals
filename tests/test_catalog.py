from decimal import Decimal
from pathlib import Path

from bazar_deals.catalog import (
    is_bulky,
    is_christmas_lighting,
    is_excluded_product,
    is_oversized,
    is_skip_keyword,
    is_too_heavy,
    reject_physical,
    stated_box_cm,
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


def test_old_dslrs_are_explicitly_excluded_but_lenses_and_new_cameras_are_not() -> None:
    assert is_excluded_product("Digitální zrcadlovka Canon EOS 400D")
    assert is_excluded_product("Canon EOS 1200D telo")
    assert is_excluded_product("Nikon D3100 DSLR")
    assert reject_physical("Digitální zrcadlovka Canon EOS 500D") == "excluded_product"
    assert not is_excluded_product("Canon EOS R50 mirrorless")
    assert not is_excluded_product("Canon EF 50mm f/1.8 objektív")


def test_reported_apple_watch_straps_never_reach_price_comparison() -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="watch-straps",
        title="Remienky Apple watch",
        description="Spigen Modern Fit Ultra.",
        url="https://mobil.bazos.sk/inzerat/watch-straps/",
        price=Money(amount=Decimal("20"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    assert run.deals == []
    assert run.funnel["drop_kind"] == 1


def test_reported_old_canon_dslr_never_reaches_price_comparison() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="canon-500d",
        title="Digitální zrcadlovka Canon EOS 500D",
        url="https://aukro.sk/canon-eos-500d/",
        price=Money(amount=Decimal("39.27"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    assert run.deals == []
    assert run.funnel["excluded_product"] == 1


def test_weight_cap_is_two_kg_and_ignores_storage() -> None:
    assert stated_weight_kg("iPhone 16GB") is None
    assert not is_too_heavy("Hmotnosť 2 kg, posielam Packeta")
    assert is_too_heavy("Hmotnosť 5 kg, posielam Packeta")
    assert is_too_heavy("Hmotnosť 6 kg")
    assert is_too_heavy("váha 5,5 kg")
    assert is_too_heavy("váha 2,1 kg")
    assert not is_too_heavy("1800 g")
    assert is_too_heavy("2500 g")
    assert reject_physical("Starý gauč") == "bulky"
    assert is_bulky("Samsung televízor 55")


def test_shoebox_longest_edge_and_sum_of_sides() -> None:
    assert stated_box_cm("krabica 50x40x30 cm") == (50.0, 40.0, 30.0)
    assert not is_oversized("50 × 40 × 30 cm, Packeta")
    assert is_oversized("51x40x30 cm")
    assert is_oversized("50x40x31 cm")
    assert reject_physical("Predám iPhone, rozmery 60x50x40 cm") == "oversized"
    assert reject_physical("Canon 50mm f/1.8, 400 g") is None


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
