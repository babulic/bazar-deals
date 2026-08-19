from decimal import Decimal

from bazar_deals.domain import IdentifiedItem, Listing, Marketplace, Money
from bazar_deals.identity import identify
from bazar_deals.valuation import estimate_resale


def _listing(title: str) -> Listing:
    return Listing(
        marketplace=Marketplace.EBAY,
        external_id="1",
        title=title,
        url="https://www.ebay.de/itm/1",
        price=Money(amount=Decimal("40"), currency="EUR"),
    )


def test_generic_iphone_has_no_resale() -> None:
    item = identify(_listing("Apple iPhone 13 128GB black"))
    assert estimate_resale(item) is None


def test_iphone_pro_does_not_inherit_a_base_seed() -> None:
    item = identify(_listing("iPhone 13 Pro Max 256"))
    assert estimate_resale(item) is None


def test_specific_commodore_keeps_seed() -> None:
    item = identify(_listing("Commodore 1541-II disk drive"))
    assert estimate_resale(item) == Decimal("89")
