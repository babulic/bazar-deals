from decimal import Decimal

from bazar_deals.config import Settings
from bazar_deals.domain import Action, CostBreakdown, Deal, IdentifiedItem, Marketplace
from bazar_deals.rules import rules


def assumed_shipping(buy: Decimal, settings: Settings | None = None) -> Decimal:
    """Assumed postage: cheaper cap under cheap_buy_eur, otherwise max_shipping_eur."""
    settings = settings or Settings()
    if buy < settings.cheap_buy_eur:
        return settings.max_shipping_cheap_eur
    return settings.max_shipping_eur


def _fee_rates() -> dict:
    return {
        Marketplace(name): Decimal(str(rate))
        for name, rate in rules()["fees"]["rates"].items()
    }


def score_deal(
    item: IdentifiedItem,
    typical: Decimal,
    shipping: Decimal | None = None,
    *,
    settings: Settings | None = None,
    min_net_profit: Decimal | None = None,
    min_margin: Decimal | None = None,
    fee_rate: Decimal | None = None,
    max_price_vs_typical: Decimal | None = None,
    max_buy_eur: Decimal | None = None,
) -> Deal:
    """BUY only when listed price is at or below typical * max_price_vs_typical.

    `typical` must be a real sold-comp median for the same working item.
    min_net_profit / min_margin are ignored for the decision (kept for call compatibility).
    Assumed postage is stored on the cost breakdown; it does not change BUY vs SKIP.
    """
    del min_net_profit, min_margin
    settings = settings or Settings()
    ratio = max_price_vs_typical if max_price_vs_typical is not None else settings.max_price_vs_typical
    cap = max_buy_eur if max_buy_eur is not None else settings.max_buy_eur
    listing = item.listing
    buy = listing.price.amount
    postage = assumed_shipping(buy, settings) if shipping is None else shipping
    fees_cfg = rules()["fees"]
    fee_rate = _fee_rates()[listing.marketplace] if fee_rate is None else fee_rate
    buy_side = {Marketplace(name) for name in fees_cfg["buy_side"]}
    fee_base = buy if listing.marketplace in buy_side else typical
    fees = (fee_base * fee_rate).quantize(Decimal("0.01"))
    if listing.marketplace in buy_side:
        fees = (fees + Decimal(str(fees_cfg["vinted_fixed_eur"]))).quantize(Decimal("0.01"))
    ceiling = (typical * ratio).quantize(Decimal("0.01"))
    delta = (typical - buy).quantize(Decimal("0.01"))
    costs = CostBreakdown(
        buy_price=buy,
        estimated_resale=typical,
        shipping=postage,
        fees=fees,
        condition_haircut=Decimal("0"),
        seller_risk=Decimal("0"),
        net_profit=delta,
    )
    if buy > cap:
        return Deal(item=item, costs=costs, action=Action.SKIP, reason=f"over max buy {cap} EUR")
    if buy > ceiling:
        return Deal(
            item=item,
            costs=costs,
            action=Action.SKIP,
            reason=f"above typical ({buy} > {ceiling})",
        )
    return Deal(
        item=item,
        costs=costs,
        action=Action.BUY,
        reason=f"at or below typical ({buy} <= {ceiling})",
    )
