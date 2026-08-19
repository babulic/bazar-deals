from decimal import Decimal

from bazar_deals.domain import Action, Deal, Vertical


def format_deal(deal: Deal) -> str:
    item = deal.item
    costs = deal.costs
    source = item.listing.marketplace.value.capitalize()
    fire = "  🔥 BUY" if deal.action is Action.BUY else f"  {deal.action.value.upper()}"
    affiliate = ""
    if item.listing.affiliate_url:
        affiliate = f"\naffiliate: {item.listing.affiliate_url}"
    typical = costs.estimated_resale
    ratio = (costs.buy_price / typical * 100).quantize(Decimal("1")) if typical else Decimal("0")
    label = item.sold_label or "obvyklá cena"
    return (
        f"{item.canonical_name}\n"
        f"{source}: {costs.buy_price} €\n"
        f"{label}: {typical} €\n"
        f"poštovné (predpoklad): {costs.shipping} €\n"
        f"pomer k obvyklej: {ratio} %{fire}\n"
        f"{item.listing.url}{affiliate}"
    )


def chat_id_for(settings, vertical: Vertical | None) -> str:
    mapping = {
        Vertical.RETRO: settings.telegram_chat_retro,
        Vertical.MINERAL: settings.telegram_chat_mineral,
        Vertical.APPLE: settings.telegram_chat_apple,
        Vertical.NETWORK: settings.telegram_chat_network,
    }
    return mapping.get(vertical, "") if vertical else ""
