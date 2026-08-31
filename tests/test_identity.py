from decimal import Decimal
from pathlib import Path
import pytest

from bazar_deals.adapters.base import ListingSource
from bazar_deals.domain import Action, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import (
    ItemKind,
    classify_kind,
    identify,
    is_replacement_part_text,
    similar_titles,
)
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient

CASSETTE_TITLE = "Vzlámavanie Konami Commodore 64/128 C64 C128"
CASSETTE_URL = "https://aukro.sk/vzlamovanie-konami-commodore-64-128-c64-c128-7089809337"
ROOT = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("accessory", [
    "Obal na Nintendo Switch Lite", "Pouzdro na Nintendo switch lite",
    "Gumki na analogi do nintendo switch lite", "Etui ochronne do Nintendo switch",
    "Nabíjací Dock Poke Ball Mini Nintendo Switch Switch Lite",
    "Data frog analog joystick na nintendo switch/lite",
    "Oryginalna Ładowarka do konsoli Nintendo Switch/Lite/Oled",
])
def test_console_is_not_valued_from_live_accessory_titles(accessory):
    assert not similar_titles("Nintendo Switch Lite", accessory)
    assert not similar_titles(accessory, "Nintendo Switch Lite")


def test_switch_variants_are_not_interchangeable():
    assert similar_titles("Nintendo Switch Lite", "Nintendo Switch Lite Grey")
    assert not similar_titles("Nintendo Switch Lite", "Nintendo Switch OLED")
    assert not similar_titles("Nintendo Switch Lite", "Nintendo Switch konzola")


class _Source(ListingSource):
    marketplace = Marketplace.AUKRO.value

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        return self._listings


def test_kinds_cover_small_goods() -> None:
    assert classify_kind(CASSETTE_TITLE) is ItemKind.MEDIA
    assert classify_kind("Obal na iPhone 13") is ItemKind.ACCESSORIES
    assert classify_kind("iPhone 13 128GB") is ItemKind.PHONES
    assert classify_kind("Pánske rifle Levi's 32") is ItemKind.CLOTHING
    assert classify_kind("Strieborné náušnice") is ItemKind.JEWELRY
    assert classify_kind("Commodore 1541-II disk drive") is ItemKind.HARDWARE
    assert classify_kind("Kniha Harry Potter") is ItemKind.BOOKS
    assert classify_kind("Surový ametyst geóda") is ItemKind.MINERALS
    assert classify_kind("crystal cluster quartz") is ItemKind.MINERALS
    assert classify_kind("Surový topás 12g") is ItemKind.MINERALS
    assert classify_kind("Alexandrit crystal specimen") is ItemKind.MINERALS
    assert classify_kind("Alexandrid surový kameň") is ItemKind.MINERALS
    assert classify_kind("Brúsený diamant 0.2ct") is ItemKind.MINERALS
    assert classify_kind("Surový rubín zafír smaragd") is ItemKind.MINERALS
    assert classify_kind("Ametystový prsteň striebro") is ItemKind.JEWELRY
    assert classify_kind("Ametystový náhrdelník") is ItemKind.JEWELRY
    assert classify_kind("Topásový prsteň") is ItemKind.JEWELRY


def test_iphone_replacement_display_is_accessory_not_phone() -> None:
    title = "Nový OLED display na iPhone 13"
    listing = Listing(
        marketplace=Marketplace.VINTED,
        external_id="display-13",
        title=title,
        description="Náhradný OLED displej, telefón nie je súčasťou ponuky.",
        url="https://www.vinted.sk/items/123456",
        price=Money(amount=Decimal("35"), currency="EUR"),
    )
    item = identify(listing)
    assert is_replacement_part_text(title)
    assert item.kind == ItemKind.ACCESSORIES.value
    assert item.kind != ItemKind.PHONES.value


def test_replacement_display_never_matches_complete_iphone_comp() -> None:
    part = "Nový OLED display na iPhone 13 128GB"
    phone = "Apple iPhone 13 128GB Midnight"
    assert similar_titles(part, phone) is False


def test_normal_iphone_is_still_phone() -> None:
    assert identify(
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="iphone",
            title="Apple iPhone 13 128GB Midnight",
            description="Plne funkčný, display bez škrabancov.",
            url="https://mobil.bazos.sk/inzerat/iphone/",
            price=Money(amount=Decimal("100"), currency="EUR"),
        )
    ).kind == ItemKind.PHONES.value


def test_konami_cassette_is_media_not_c64_computer() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="7089809337",
        title=CASSETTE_TITLE,
        url=CASSETTE_URL,
        price=Money(amount=Decimal("12.62"), currency="EUR"),
    )
    assert classify_kind(CASSETTE_TITLE) is ItemKind.MEDIA
    item = identify(listing)
    assert item.confidence >= 0.5
    assert "konami" in (item.search_query or "")


def test_cassette_does_not_buy_against_c64_computer_sold_comps() -> None:
    listing = Listing(
        marketplace=Marketplace.AUKRO,
        external_id="7089809337",
        title=CASSETTE_TITLE,
        url=CASSETTE_URL,
        price=Money(amount=Decimal("12.62"), currency="EUR"),
    )
    sold = SoldCompClient(fixture_path=ROOT / "ebay_sold_c64_computers.html")
    deals = hunt(_Source([listing]), sold=sold)
    assert deals == []
    assert all(deal.action is not Action.BUY for deal in deals)


def test_1541_drive_matches_1541_sold_comps() -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1541",
        title="Commodore 1541-II disk drive",
        description="Funkčná mechanika, krabica.",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal("38"), currency="EUR"),
    )
    comp = SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html").median_sold(listing)
    assert comp is not None
    assert comp.reliable_for_buy is True
    assert comp.median >= Decimal("80")
