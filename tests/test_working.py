from decimal import Decimal
from pathlib import Path

from bazar_deals.adapters.base import ListingSource
from bazar_deals.domain import Condition, Listing, Marketplace, Money, Vertical
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient
from bazar_deals.working import is_damaged_text, is_working_listing

SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


class _Source(ListingSource):
    marketplace = Marketplace.EBAY.value

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        return self._listings


def test_for_parts_and_broken_are_damaged() -> None:
    assert is_damaged_text("iPhone 13 na diely")
    assert is_damaged_text("MacBook for parts not working")
    assert not is_damaged_text("iPhone 13, bez poskodenia, tested working")


def test_hunt_skips_damaged_even_if_cheap() -> None:
    listing = Listing(
        marketplace=Marketplace.EBAY,
        external_id="9",
        title="Commodore 1541-II disk drive na diely",
        url="https://www.ebay.de/itm/9",
        price=Money(amount=Decimal("10"), currency="EUR"),
        condition=Condition.FOR_PARTS,
    )
    assert is_working_listing(listing) is False
    deals = hunt(_Source([listing]), sold=SoldCompClient(fixture_path=SOLD))
    assert deals == []
