from decimal import Decimal

from bazar_deals.config import Settings
from bazar_deals.domain import Action, CostBreakdown, Deal, IdentifiedItem, Marketplace


FEE_RATES = {
    Marketplace.EBAY: Decimal("0.13"),
    Marketplace.AUKRO: Decimal("0.11"),
    Marketplace.BAZOS: Decimal("0"),
    Marketplace.VINTED: Decimal("0.05"),
}

BUY_SIDE_FEE = {Marketplace.VINTED}


def score_deal(
    item: IdentifiedItem,
    typical: Decimal,
    shipping: Decimal = Decimal("0"),
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
    """
    del min_net_profit, min_margin
    settings = settings or Settings()
    ratio = max_price_vs_typical if max_price_vs_typical is not None else settings.max_price_vs_typical
    cap = max_buy_eur if max_buy_eur is not None else settings.max_buy_eur
    listing = item.listing
    buy = listing.price.amount
    fee_rate = FEE_RATES[listing.marketplace] if fee_rate is None else fee_rate
    fee_base = buy if listing.marketplace in BUY_SIDE_FEE else typical
    fees = (fee_base * fee_rate).quantize(Decimal("0.01"))
    if listing.marketplace is Marketplace.VINTED:
        fees = (fees + Decimal("0.70")).quantize(Decimal("0.01"))
    ceiling = (typical * ratio).quantize(Decimal("0.01"))
    delta = (typical - buy).quantize(Decimal("0.01"))
    costs = CostBreakdown(
        buy_price=buy,
        estimated_resale=typical,
        shipping=shipping,
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
