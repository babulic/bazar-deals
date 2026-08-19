from decimal import Decimal

from bazar_deals.adapters.base import ListingSource
from bazar_deals.adapters.ebay import is_ebay_buy_now
from bazar_deals.domain import Listing, Marketplace, Money, Vertical
from bazar_deals.htmlparse import parse_json_ld_products
from bazar_deals.pipeline import hunt


def _commodore(*, buy_now: bool, bid_count: int | None = None) -> Listing:
    return Listing(
        marketplace=Marketplace.EBAY,
        external_id="1",
        title="Commodore 1541-II disk drive",
        url="https://www.ebay.de/itm/1",
        price=Money(amount=Decimal("38"), currency="EUR"),
        buy_now=buy_now,
        bid_count=bid_count,
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
    deals = hunt(_Source([_commodore(buy_now=False)]))
    assert deals == []


def test_hunt_keeps_buy_now() -> None:
    deals = hunt(_Source([_commodore(buy_now=True)]))
    assert deals
    assert deals[0].item.listing.buy_now is True


def test_json_ld_auction_offer_is_not_buy_now() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"iPhone 13","url":"https://aukro.sk/iphone-13-111",'
        '"offers":{"@type":"Auction","price":"1","priceCurrency":"EUR"}}'
        "</script>"
    )
    listings = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")
    assert listings[0].buy_now is False
