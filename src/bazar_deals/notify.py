from __future__ import annotations

import re
from decimal import Decimal

from bazar_deals.domain import Action, Deal, Vertical

_KIND_SK = {
    "media": "médiá",
    "books": "knihy",
    "accessories": "príslušenstvo",
    "clothing": "oblečenie",
    "jewelry": "šperky",
    "minerals": "minerály",
    "collectibles": "zberateľské predmety",
    "bags": "tašky",
    "kitchen": "kuchyňa",
    "stationery": "kancelária",
    "cosmetics": "kozmetika",
    "toys": "hračky",
    "sports": "šport",
    "photo": "foto",
    "phones": "telefóny",
    "musical": "hudobné nástroje",
    "tools": "náradie",
    "hardware": "hardware",
    "generic": "iné",
}

_CONDITION_SK = {
    "new": "nový",
    "like_new": "ako nový",
    "used": "použitý",
    "for_parts": "na diely",
    "unknown": "neznámy",
}


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


def format_github_deal(deal: Deal) -> str:
    """Pretty key: value markdown for GitHub issue comments (clickable listing link)."""
    item = deal.item
    listing = item.listing
    costs = deal.costs
    typical = costs.estimated_resale
    url = str(listing.url)
    title = listing.title or item.canonical_name
    kind = _kind_label(item.kind)
    rows: list[tuple[str, str]] = [
        ("názov", title),
        ("typ tovaru", kind),
    ]
    if item.canonical_name and item.canonical_name != title:
        rows.append(("canonical", item.canonical_name))
    if item.brand:
        rows.append(("značka", item.brand))
    if item.model:
        rows.append(("model", item.model))
    if item.vertical is not None:
        rows.append(("vertikála", item.vertical.value))
    rows.append(("marketplace", listing.marketplace.value))
    rows.append(("id", listing.external_id))
    rows.append(("cena", _eur(costs.buy_price)))
    if typical > 0:
        rows.append(("typická cena", _eur(typical)))
        ratio = (costs.buy_price / typical * 100).quantize(Decimal("1"))
        rows.append(("pomer k obvyklej", f"{ratio} %"))
    else:
        rows.append(("typická cena", "neznáma (chýbajú predané/trhové comps)"))
    rows.append(("poštovné (predpoklad)", _eur(costs.shipping)))
    if costs.fees > 0:
        rows.append(("poplatky", _eur(costs.fees)))
    if typical > 0:
        rows.append(("rozdiel vs obvyklá", _eur(costs.net_profit)))
    rows.append(("stav", _CONDITION_SK.get(listing.condition.value, listing.condition.value)))
    if listing.location:
        rows.append(("lokalita", listing.location))
    if listing.seller_id:
        rows.append(("predajca", listing.seller_id))
    if listing.seller_score is not None:
        rows.append(("seller_score", f"{listing.seller_score:.2f}"))
    if listing.created_at:
        rows.append(("created_at", listing.created_at.isoformat()))
    if listing.ends_at:
        rows.append(("ends_at", listing.ends_at.isoformat()))
    if listing.bid_count:
        rows.append(("bid_count", str(listing.bid_count)))
    if not listing.buy_now:
        rows.append(("kúpiť teraz", "nie"))
    identity = item.search_query or listing.search_query
    rows.append(("identita", identity or "—"))
    rows.append(("confidence", f"{item.confidence:.2f}"))
    if item.asking_sample > 0:
        rows.append(("sample predaných", str(item.asking_sample)))
    else:
        rows.append(("sample predaných", "chýba"))
    rows.append(("sold_label", item.sold_label or "—"))
    rows.append(("dôvod", _quiet_reason(deal.reason)))
    rows.append(("inzerát", f"[inzerát]({url})"))
    if listing.affiliate_url:
        rows.append(("affiliate", f"[affiliate]({listing.affiliate_url})"))
    heading = f"**{_md_link(title, url)}** · {kind}"
    body = "\n".join(f"- {key}: {value}" for key, value in rows)
    return f"{heading}\n\n{body}"


def chat_id_for(settings, vertical: Vertical | None) -> str:
    mapping = {
        Vertical.RETRO: settings.telegram_chat_retro,
        Vertical.MINERAL: settings.telegram_chat_mineral,
        Vertical.APPLE: settings.telegram_chat_apple,
        Vertical.NETWORK: settings.telegram_chat_network,
    }
    return mapping.get(vertical, "") if vertical else ""


def _kind_label(kind: str) -> str:
    raw = (kind or "generic").strip() or "generic"
    human = _KIND_SK.get(raw, raw)
    if human == raw:
        return raw
    return f"{human} ({raw})"


def _quiet_reason(reason: str) -> str:
    text = (reason or "").replace("🔥", "")
    text = re.sub(r"\b(ALERT|BUY)\b", "", text, flags=re.I)
    return " ".join(text.split()) or "—"


def _eur(amount: Decimal) -> str:
    return f"{amount} €"


def _md_link(label: str, url: str) -> str:
    safe = label.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe}]({url})"
