from decimal import Decimal
from pathlib import Path

from bazar_deals.adapters.base import ListingSource
from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.domain import Action, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import ItemKind, classify_kind, identify
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient

CASSETTE_TITLE = "Vzlámavanie Konami Commodore 64/128 C64 C128"
CASSETTE_URL = "https://aukro.sk/vzlamovanie-konami-commodore-64-128-c64-c128-7089809337"
ROOT = Path(__file__).parent / "fixtures"


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
    assert all(deal.action is not Action.BUY for deal in deals)
    assert deals
    assert deals[0].action is Action.ALERT
    assert "sold" in deals[0].reason


def test_1541_drive_can_buy_when_sold_comps_match() -> None:
    sold = SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html")
    deals = hunt(BazosRssClient(fixture_path=ROOT / "bazos_rss.xml"), sold=sold)
    cheap = [deal for deal in deals if "1541/" in str(deal.item.listing.url)]
    assert cheap
    assert cheap[0].action is Action.BUY
    assert cheap[0].costs.estimated_resale >= Decimal("80")
    assert "predaných" in cheap[0].item.sold_label or "sold" in cheap[0].item.sold_label
