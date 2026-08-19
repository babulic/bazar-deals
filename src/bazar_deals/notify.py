from bazar_deals.domain import Action, Deal, Vertical


def format_deal(deal: Deal) -> str:
    item = deal.item
    costs = deal.costs
    source = item.listing.marketplace.value.capitalize()
    flag = {Action.BUY: "BUY", Action.ALERT: "WATCH", Action.SKIP: "SKIP"}[deal.action]
    fire = "  🔥 BUY" if deal.action is Action.BUY else f"  {flag}"
    affiliate = ""
    if item.listing.affiliate_url:
        affiliate = f"\naffiliate: {item.listing.affiliate_url}"
    return (
        f"{item.canonical_name}\n"
        f"{source}: {costs.buy_price} €\n"
        f"odhad resale: {costs.estimated_resale} €\n"
        f"shipping + fees: {costs.shipping + costs.fees} €\n"
        f"estimated profit: {costs.net_profit} €{fire}\n"
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
