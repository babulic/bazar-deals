from decimal import Decimal

from bazar_deals.domain import Action, CostBreakdown, Deal, IdentifiedItem, Marketplace


FEE_RATES = {
    Marketplace.EBAY: Decimal("0.13"),
    Marketplace.AUKRO: Decimal("0.11"),
    Marketplace.BAZOS: Decimal("0"),
    Marketplace.VINTED: Decimal("0.05"),
}

# Buyer Protection is charged on the purchase, not on later resale.
BUY_SIDE_FEE = {Marketplace.VINTED}

CONDITION_HAIRCUT = {
    "new": Decimal("0"),
    "like_new": Decimal("0.05"),
    "used": Decimal("0.12"),
    "for_parts": Decimal("0.55"),
    "unknown": Decimal("0.18"),
}


def score_deal(
    item: IdentifiedItem,
    estimated_resale: Decimal,
    shipping: Decimal,
    *,
    min_net_profit: Decimal = Decimal("20"),
    min_margin: Decimal = Decimal("0.25"),
    fee_rate: Decimal | None = None,
) -> Deal:
    listing = item.listing
    buy = listing.price.amount
    fee_rate = FEE_RATES[listing.marketplace] if fee_rate is None else fee_rate
    fee_base = buy if listing.marketplace in BUY_SIDE_FEE else estimated_resale
    fees = (fee_base * fee_rate).quantize(Decimal("0.01"))
    if listing.marketplace is Marketplace.VINTED:
        fees = (fees + Decimal("0.70")).quantize(Decimal("0.01"))
    haircut = (estimated_resale * CONDITION_HAIRCUT[listing.condition.value]).quantize(
        Decimal("0.01")
    )
    seller_risk = Decimal("0")
    if listing.seller_score is None:
        seller_risk = (estimated_resale * Decimal("0.04")).quantize(Decimal("0.01"))
    elif listing.seller_score < 0.9:
        seller_risk = (estimated_resale * Decimal("0.08")).quantize(Decimal("0.01"))

    net = (estimated_resale - buy - shipping - fees - haircut - seller_risk).quantize(
        Decimal("0.01")
    )
    costs = CostBreakdown(
        buy_price=buy,
        estimated_resale=estimated_resale,
        shipping=shipping,
        fees=fees,
        condition_haircut=haircut,
        seller_risk=seller_risk,
        net_profit=net,
    )

    margin = net / buy if buy else Decimal("0")
    if net >= min_net_profit and margin >= min_margin:
        action = Action.BUY
        reason = f"estimated profit: {net} EUR"
    elif net > 0:
        action = Action.ALERT
        reason = f"thin edge: {net} EUR"
    else:
        action = Action.SKIP
        reason = f"no edge: {net} EUR"

    return Deal(item=item, costs=costs, action=action, reason=reason)
