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
    label = item.sold_label or "obvyklá cena"
    if typical > 0:
        ratio = (costs.buy_price / typical * 100).quantize(Decimal("1"))
        typical_line = f"{label}: {typical} €\n"
        ratio_line = f"pomer k obvyklej: {ratio} %{fire}\n"
    else:
        typical_line = f"{label}\n"
        ratio_line = f"{fire.strip()}\n"
    return (
        f"{item.canonical_name}\n"
        f"{source}: {costs.buy_price} €\n"
        f"{typical_line}"
        f"poštovné (predpoklad): {costs.shipping} €\n"
        f"{ratio_line}"
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
