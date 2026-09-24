from decimal import Decimal

from bazar_deals.config import Settings
from bazar_deals.domain import Condition, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.scoring import assumed_shipping, score_deal
from bazar_deals.watchlist import MIN_BATTERY_HEALTH_PERCENT
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


def test_buy_requires_configured_net_profit_floor() -> None:
    settings = Settings()
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("120"), Decimal("8"), settings=settings)
    assert deal.action.value == "buy"
    assert deal.costs.net_profit >= settings.min_net_profit_eur


def test_estimate_net_profit_matches_score_deal() -> None:
    from bazar_deals.scoring import estimate_net_profit

    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("120"), Decimal("8"))
    assert estimate_net_profit(item, Decimal("120"), shipping=Decimal("8")) == deal.costs.net_profit


def test_buy_just_above_the_net_profit_floor() -> None:
    settings = Settings()
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("70"), Decimal("8"), settings=settings)
    assert deal.action.value == "buy"
    assert deal.costs.net_profit >= settings.min_net_profit_eur
    assert deal.costs.net_profit < settings.min_net_profit_eur + Decimal("10")
    below = score_deal(item, Decimal("64"), Decimal("8"), settings=settings)
    assert below.action.value == "skip"
    assert below.costs.net_profit < settings.min_net_profit_eur


def test_vinted_includes_buyer_protection_and_resale_fee_reserve() -> None:
    from bazar_deals.rules import rules

    listing = _listing("38").model_copy(update={"marketplace": Marketplace.VINTED})
    item = identify(listing, Vertical.APPLE)
    settings = Settings()
    deal = score_deal(item, Decimal("120"), Decimal("8"), settings=settings)
    buy = Decimal("38")
    typical = Decimal("120")
    fixed = Decimal(str(rules()["fees"]["vinted_fixed_eur"]))
    purchase = (buy * settings.vinted_fee_rate + fixed).quantize(Decimal("0.01"))
    resale = (typical * settings.resale_fee_rate).quantize(Decimal("0.01"))
    assert deal.costs.fees == purchase + resale


def test_battery_under_threshold_and_no_box_reduce_resale_value() -> None:
    from bazar_deals.watchlist import BATTERY_UNDER_PCT

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="iphone",
        title="Apple iPhone SE 2020 64 GB",
        description=f"Batéria {BATTERY_UNDER_PCT - 3} %, bez krabičky, plne funkčný.",
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
    expected = (Decimal("70") * settings.battery_under_80_haircut_rate) + settings.no_box_haircut_eur
    assert deal.costs.condition_haircut == expected
    assert deal.action.value == "skip"
    assert f"{BATTERY_UNDER_PCT - 3}% < {MIN_BATTERY_HEALTH_PERCENT}%" in deal.reason


def test_battery_health_below_minimum_is_rejected() -> None:
    low = identify(
        _listing("38", description=f"Plne funkčný, batéria {MIN_BATTERY_HEALTH_PERCENT - 1} %."),
        Vertical.APPLE,
    )
    minimum = identify(
        _listing("38", description=f"Plne funkčný, batéria {MIN_BATTERY_HEALTH_PERCENT} %."),
        Vertical.APPLE,
    )
    assert score_deal(low, Decimal("120"), Decimal("8")).action.value == "skip"
    assert score_deal(minimum, Decimal("120"), Decimal("8")).action.value == "buy"


def test_structured_battery_health_below_minimum_is_rejected() -> None:
    low = _listing("38").model_copy(
        update={"raw": {"batteryHealth": f"{MIN_BATTERY_HEALTH_PERCENT - 1}%"}}
    )
    minimum = _listing("38").model_copy(
        update={"raw": {"batteryHealth": f"{MIN_BATTERY_HEALTH_PERCENT}%"}}
    )
    assert is_working_listing(low) is False
    assert is_working_listing(minimum) is True


def test_custom_net_profit_floor() -> None:
    item = identify(_listing("38"), Vertical.RETRO)
    deal = score_deal(item, Decimal("100"), Decimal("8"), min_net_profit=Decimal("50"))
    assert deal.action.value == "skip"


def test_cheap_buy_uses_cheaper_shipping_cap() -> None:
    settings = Settings()
    under = settings.cheap_buy_eur - Decimal("2")
    over = settings.cheap_buy_eur + Decimal("18")
    assert assumed_shipping(under) == settings.max_shipping_cheap_eur
    assert assumed_shipping(over) == settings.max_shipping_eur


def test_max_buy_cap_still_applies() -> None:
    item = IdentifiedItem(
        listing=_listing("200"),
        vertical=Vertical.RETRO,
        canonical_name="Commodore 1541-II",
        confidence=0.9,
    )
    deal = score_deal(item, Decimal("400"), Decimal("8"))
    assert deal.action.value == "skip"
