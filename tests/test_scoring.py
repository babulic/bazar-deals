from decimal import Decimal

from bazar_deals.config import Settings
from bazar_deals.domain import Condition, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.scoring import assumed_shipping, score_deal
from bazar_deals.working import is_working_listing


def _listing(price: str = "38", *, description: str = "") -> Listing:
    return Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1",
        title="Commodore 1541-II",
        description=description,
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal(price), currency="EUR"),
        condition=Condition.USED,
    )


def test_buy_requires_at_least_20_eur_expected_net_profit() -> None:
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("120"), Decimal("8"))
    assert deal.action.value == "buy"
    assert deal.costs.net_profit >= Decimal("20")


def test_estimate_net_profit_matches_score_deal() -> None:
    from bazar_deals.scoring import estimate_net_profit

    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("120"), Decimal("8"))
    assert estimate_net_profit(item, Decimal("120"), shipping=Decimal("8")) == deal.costs.net_profit


def test_buy_between_20_and_30_uses_the_new_floor() -> None:
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("89"), Decimal("8"))
    assert deal.action.value == "buy"
    assert deal.costs.net_profit >= Decimal("20")
    assert deal.costs.net_profit < Decimal("30")


def test_vinted_includes_buyer_protection_and_resale_fee_reserve() -> None:
    listing = _listing("38").model_copy(update={"marketplace": Marketplace.VINTED})
    item = identify(listing, Vertical.APPLE)
    deal = score_deal(item, Decimal("120"), Decimal("8"))
    # 5% + 0.70 purchase fee = 2.60; 10% resale reserve = 12.00.
    assert deal.costs.fees == Decimal("14.60")


def test_battery_under_80_and_no_box_reduce_resale_value() -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="iphone",
        title="Apple iPhone SE 2020 64 GB",
        description="Batéria 77 %, bez krabičky, plne funkčný.",
        url="https://mobil.bazos.sk/inzerat/1/iphone.php",
        price=Money(amount=Decimal("40"), currency="EUR"),
        condition=Condition.USED,
    )
    item = identify(listing, Vertical.APPLE)
    settings = Settings(
        resale_fee_rate=Decimal("0"),
        seller_risk_reserve_rate=Decimal("0"),
    )
    deal = score_deal(item, Decimal("70"), Decimal("0"), settings=settings)
    assert deal.costs.condition_haircut == Decimal("15.50")
    assert deal.action.value == "skip"
    assert "77% < 84%" in deal.reason


def test_battery_health_83_is_rejected_and_84_is_allowed() -> None:
    low = identify(_listing("38", description="Plne funkčný, batéria 83 %."), Vertical.APPLE)
    minimum = identify(_listing("38", description="Plne funkčný, batéria 84 %."), Vertical.APPLE)
    assert score_deal(low, Decimal("120"), Decimal("8")).action.value == "skip"
    assert score_deal(minimum, Decimal("120"), Decimal("8")).action.value == "buy"


def test_structured_battery_health_below_84_is_rejected() -> None:
    low = _listing("38").model_copy(update={"raw": {"batteryHealth": "83%"}})
    minimum = _listing("38").model_copy(update={"raw": {"batteryHealth": "84%"}})
    assert is_working_listing(low) is False
    assert is_working_listing(minimum) is True


def test_custom_net_profit_floor() -> None:
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("100"), Decimal("8"), min_net_profit=Decimal("50"))
    assert deal.action.value == "skip"


def test_cheap_buy_uses_cheaper_shipping_cap() -> None:
    assert assumed_shipping(Decimal("18")) == Decimal("11")
    assert assumed_shipping(Decimal("38")) == Decimal("15")


def test_max_buy_cap_still_applies() -> None:
    item = IdentifiedItem(
        listing=_listing("200"),
        vertical=Vertical.RETRO,
        canonical_name="Commodore 1541-II",
        confidence=0.9,
    )
    deal = score_deal(item, Decimal("400"), Decimal("8"))
    assert deal.action.value == "skip"
