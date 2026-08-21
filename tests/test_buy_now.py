from decimal import Decimal
from pathlib import Path

from bazar_deals.adapters.base import ListingSource
from bazar_deals.adapters.ebay import is_ebay_buy_now
from bazar_deals.domain import Listing, Marketplace, Money, Vertical
from bazar_deals.htmlparse import parse_json_ld_products
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient

SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


def _drive(
    *,
    buy_now: bool,
    external_id: str = "1",
    price: str = "38",
    ships_to_slovakia: bool | None = True,
) -> Listing:
    return Listing(
        marketplace=Marketplace.EBAY,
        external_id=external_id,
        title="Commodore 1541-II disk drive",
        description="Funkčný disk drive, otestovaný a bez známych chýb.",
        url=f"https://www.ebay.de/itm/{external_id}",
        price=Money(amount=Decimal(price), currency="EUR"),
        buy_now=buy_now,
        ships_to_slovakia=ships_to_slovakia,
    )


class _Source(ListingSource):
    marketplace = Marketplace.EBAY.value

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        return self._listings


def test_ebay_drops_auction_even_with_bin() -> None:
    assert is_ebay_buy_now({"buyingOptions": ["AUCTION", "FIXED_PRICE"]}) is False
    assert is_ebay_buy_now({"buyingOptions": ["FIXED_PRICE"]}) is True
    assert is_ebay_buy_now({"buyingOptions": ["FIXED_PRICE"], "bidCount": 3}) is False
    assert is_ebay_buy_now({"buyingOptions": ["AUCTION"], "currentBidPrice": {"value": "1"}}) is False


def test_hunt_skips_auctions() -> None:
    sold = SoldCompClient(fixture_path=SOLD)
    deals = hunt(_Source([_drive(buy_now=False)]), sold=sold)
    assert deals == []


def test_hunt_keeps_buy_now_for_scoring() -> None:
    sold = SoldCompClient(fixture_path=SOLD)
    deals = hunt(_Source([_drive(buy_now=True)]), sold=sold)
    assert deals
    assert deals[0].item.listing.buy_now is True


def test_hunt_rejects_ebay_when_slovakia_delivery_is_not_confirmed() -> None:
    sold = SoldCompClient(fixture_path=SOLD)
    assert hunt(_Source([_drive(buy_now=True, ships_to_slovakia=None)]), sold=sold) == []
    assert hunt(_Source([_drive(buy_now=True, ships_to_slovakia=False)]), sold=sold) == []


def test_json_ld_auction_offer_is_not_buy_now() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"iPhone 13","url":"https://aukro.sk/iphone-13-111",'
        '"offers":{"@type":"Auction","price":"1","priceCurrency":"EUR"}}'
        "</script>"
    )
    listings = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
    assert listings[0].buy_now is False


def test_price_cap_drops_over_110() -> None:
    sold = SoldCompClient(fixture_path=SOLD)
    deals = hunt(_Source([_drive(buy_now=True, price="200")]), sold=sold)
    assert deals == []
