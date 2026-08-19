from decimal import Decimal

from bazar_deals.domain import Condition, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.scoring import score_deal


def _listing(price: str = "38") -> Listing:
    return Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1",
        title="Commodore 1541-II",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal(price), currency="EUR"),
        condition=Condition.USED,
    )


def test_buy_when_at_or_below_typical() -> None:
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("89"), Decimal("8"))
    assert deal.action.value == "buy"
    assert deal.costs.buy_price <= deal.costs.estimated_resale


def test_vinted_fees_are_buyer_protection_on_purchase() -> None:
    listing = _listing("38").model_copy(update={"marketplace": Marketplace.VINTED})
    item = identify(listing, Vertical.APPLE)
    deal = score_deal(item, Decimal("89"), Decimal("8"))
    assert deal.costs.fees == Decimal("2.60")


def test_skip_when_buy_price_is_above_typical() -> None:
    item = IdentifiedItem(
        listing=_listing("90"),
        vertical=Vertical.RETRO,
        canonical_name="Commodore 1541-II",
        confidence=0.9,
    )
    deal = score_deal(item, Decimal("89"), Decimal("8"))
    assert deal.action.value == "skip"


def test_equal_to_typical_is_buy() -> None:
    item = identify(_listing("50"), Vertical.RETRO)
    assert score_deal(item, Decimal("50")).action.value == "buy"


def test_price_vs_typical_ratio_is_configurable() -> None:
    item = identify(_listing("50"), Vertical.RETRO)
    deal = score_deal(item, Decimal("89"), max_price_vs_typical=Decimal("0.5"))
    assert deal.action.value == "skip"
