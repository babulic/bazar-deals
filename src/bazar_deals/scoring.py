from __future__ import annotations

import re
from decimal import Decimal, ROUND_CEILING

from bazar_deals.adapters.central_europe import SITES
from bazar_deals.config import Settings
from bazar_deals.domain import Action, CostBreakdown, Deal, IdentifiedItem, Marketplace
from bazar_deals.rules import rules


def assumed_shipping(buy: Decimal, settings: Settings | None = None) -> Decimal:
    """Conservative inbound postage when the listing does not expose a real cost."""
    settings = settings or Settings()
    if buy < settings.cheap_buy_eur:
        return settings.max_shipping_cheap_eur
    return settings.max_shipping_eur


def _vinted_buy_fee(buy: Decimal, settings: Settings) -> Decimal:
    fees_cfg = rules()["fees"]
    rate = Decimal(str(fees_cfg["rates"]["vinted"]))
    fixed = Decimal(str(fees_cfg["vinted_fixed_eur"]))
    return (buy * rate + fixed).quantize(Decimal("0.01"))


def _battery_health(text: str) -> int | None:
    folded = text.casefold()
    patterns = (
        r"(?:battery\s*health|battery|bat[eé]ri[ae]|bateria|akku|kond[ií]cia\s*bat[eé]rie)[^\d]{0,20}(\d{2,3})\s*%",
        r"(\d{2,3})\s*%[^\n]{0,20}(?:battery|bat[eé]ri[ae]|bateria|akku)",
    )
    for pattern in patterns:
        match = re.search(pattern, folded, flags=re.I)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 100:
                return value
    return None


def condition_haircut(item: IdentifiedItem, resale: Decimal, settings: Settings) -> Decimal:
    """Known listing-specific defects/accessory gaps reduce the conservative resale value."""
    listing = item.listing
    text = f"{listing.title} {listing.description}".casefold()
    haircut = Decimal("0")

    battery = _battery_health(text)
    if battery is not None:
        if battery < 80:
            haircut += resale * settings.battery_under_80_haircut_rate
        elif battery < 85:
            haircut += resale * settings.battery_80_84_haircut_rate
        elif battery < 90:
            haircut += resale * settings.battery_85_89_haircut_rate

    no_box_markers = (
        "bez krabice",
        "bez krabičky",
        "bez krabicky",
        "without box",
        "no box",
        "ohne ovp",
        "ohne originalverpackung",
    )
    if any(marker in text for marker in no_box_markers):
        haircut += settings.no_box_haircut_eur

    return min(resale, haircut.quantize(Decimal("0.01")))


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
    alert_price_vs_typical: Decimal | None = None,
    max_buy_eur: Decimal | None = None,
) -> Deal:
    """BUY only when conservative expected net profit is at least the configured floor.

    `typical` is expected to already be a conservative quick-sale comp value.  The
    old price-ratio rule is intentionally ignored: cheap versus a bad valuation is
    not a deal.  We subtract inbound shipping, purchase fees, a conservative resale
    fee reserve, listing-specific condition haircuts, and a general risk reserve.
    """
    del min_margin, fee_rate, max_price_vs_typical, alert_price_vs_typical
    settings = settings or Settings()
    threshold = min_net_profit if min_net_profit is not None else settings.min_net_profit_eur
    cap = max_buy_eur if max_buy_eur is not None else settings.max_buy_eur
    listing = item.listing
    buy = listing.price.amount
    postage = assumed_shipping(buy, settings) if shipping is None else shipping

    purchase_fee = Decimal("0")
    if listing.marketplace is Marketplace.VINTED:
        purchase_fee = _vinted_buy_fee(buy, settings)
    resale_fee = (typical * settings.resale_fee_rate).quantize(Decimal("0.01"))
    fx_base = Decimal("0")
    if listing.raw.get("original_price_currency", listing.price.currency).upper() in {"CZK", "PLN"}:
        fx_base += buy
    if listing.raw.get("original_shipping_currency", listing.shipping_cost.currency if listing.shipping_cost else "EUR").upper() in {"CZK", "PLN"}:
        fx_base += postage
    fx_reserve = (fx_base * settings.fx_fee_rate).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    fees = (purchase_fee + resale_fee + fx_reserve).quantize(Decimal("0.01"))
    haircut = condition_haircut(item, typical, settings)
    risk = (typical * settings.seller_risk_reserve_rate).quantize(Decimal("0.01"))
    net = (typical - buy - postage - fees - haircut - risk).quantize(Decimal("0.01"))

    costs = CostBreakdown(
        buy_price=buy,
        estimated_resale=typical,
        shipping=postage,
        fees=fees,
        condition_haircut=haircut,
        seller_risk=risk,
        net_profit=net,
        fx_fee_reserve=fx_reserve,
    )

    if not listing.purchase_allowed(require_confirmation=listing.marketplace.value in SITES):
        return Deal(item=item, costs=costs, action=Action.SKIP, reason="Delivery to Slovakia not verified")
    if not listing.is_immediate_buy():
        return Deal(item=item, costs=costs, action=Action.SKIP, reason="Not an available fixed-price offer")
    if buy > cap:
        return Deal(item=item, costs=costs, action=Action.SKIP, reason=f"over max buy {cap} EUR")
    if buy < settings.min_buy_eur:
        return Deal(item=item, costs=costs, action=Action.SKIP, reason=f"under min buy {settings.min_buy_eur} EUR")
    if net >= threshold:
        return Deal(
            item=item,
            costs=costs,
            action=Action.BUY,
            reason=f"expected net profit {net} EUR >= {threshold} EUR",
        )
    return Deal(
        item=item,
        costs=costs,
        action=Action.SKIP,
        reason=f"expected net profit {net} EUR < {threshold} EUR",
    )
