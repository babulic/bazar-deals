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
    "other": "iné",
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
    listing = item.listing
    costs = deal.costs
    source = listing.marketplace.value.capitalize()
    affiliate = ""
    if listing.affiliate_url:
        affiliate = f"\naffiliate: {listing.affiliate_url}"
    shipping_note = ""
    if listing.marketplace.value == "ebay":
        shipping_note = " (doručenie na Slovensko potvrdené)"
    ai_lines = ""
    if deal.ai_review:
        ai = deal.ai_review
        ai_price = f"{ai.quick_sale_price_eur} €" if ai.quick_sale_price_eur is not None else "neoverená"
        ai_lines = (
            f"AI identifikácia: {ai.canonical_name}\n"
            f"AI web cena: {ai_price}; confidence {ai.confidence:.2f}\n"
            f"AI dôvod: {ai.reason}\n"
        )
    return (
        f"Titulok inzerátu: {listing.title}\n"
        f"Identifikovaný tovar: {item.canonical_name}\n"
        f"{ai_lines}"
        f"{source}: {costs.buy_price} €\n"
        f"{item.sold_label or 'konzervatívna predajná cena'}: {costs.estimated_resale} €\n"
        f"nákupná doprava{shipping_note}: {costs.shipping} €\n"
        f"poplatky a resale rezerva: {costs.fees} €\n"
        f"stav/výbava haircut: {costs.condition_haircut} €\n"
        f"riziková rezerva: {costs.seller_risk} €\n"
        f"očakávaný čistý zisk: {costs.net_profit} €  🔥 BUY\n"
        f"{listing.url}{affiliate}"
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
        ("titulok inzerátu", title),
        ("identifikovaný tovar", item.canonical_name or "—"),
        ("typ tovaru", kind),
    ]
    if item.brand:
        rows.append(("značka", item.brand))
    if item.model:
        rows.append(("model", item.model))
    if item.vertical is not None:
        rows.append(("vertikála", item.vertical.value))
    rows.append(("marketplace", listing.marketplace.value))
    rows.append(("id", listing.external_id))
    rows.append(("nákupná cena", _eur(costs.buy_price)))
    if typical > 0:
        rows.append(("finálna konzervatívna rýchlopredajná cena", _eur(typical)))
    else:
        rows.append(("finálna konzervatívna rýchlopredajná cena", "neznáma"))
    rows.append(("nákupná doprava", _eur(costs.shipping)))
    rows.append(("poplatky + resale rezerva", _eur(costs.fees)))
    if costs.condition_haircut > 0:
        rows.append(("haircut za stav/výbavu", _eur(costs.condition_haircut)))
    rows.append(("riziková rezerva", _eur(costs.seller_risk)))
    rows.append(("očakávaný čistý zisk", _eur(costs.net_profit)))
    if listing.marketplace.value == "ebay":
        rows.append(("doručenie na Slovensko", "áno" if listing.ships_to_slovakia else "neoverené"))
    rows.append(("stav", _CONDITION_SK.get(listing.condition.value, listing.condition.value)))
    if listing.location:
        rows.append(("lokalita", listing.location))
    if listing.seller_id:
        rows.append(("predajca", listing.seller_id))
    if listing.seller_score is not None:
        rows.append(("seller_score", f"{listing.seller_score:.2f}"))
    identity = item.search_query or listing.search_query
    rows.append(("deterministická identita", identity or "—"))
    rows.append(("deterministická confidence", f"{item.confidence:.2f}"))
    if item.asking_sample > 0:
        rows.append(("sample predaných", str(item.asking_sample)))
    else:
        rows.append(("sample predaných", "chýba"))
    rows.append(("price source", item.sold_label or "—"))

    if deal.ai_review:
        ai = deal.ai_review
        rows.append(("AI identifikácia", ai.canonical_name))
        rows.append(("AI complete product", "áno" if ai.complete_product else "nie"))
        rows.append(("AI confidence", f"{ai.confidence:.2f}"))
        if ai.quick_sale_price_eur is not None:
            rows.append(("AI web quick-sale cena", _eur(ai.quick_sale_price_eur)))
        rows.append(("AI model", ai.model + (" (cache)" if ai.cached else "")))
        rows.append(("AI dôvod", ai.reason or "—"))
        if ai.source_urls:
            links = " · ".join(f"[zdroj {index + 1}]({source})" for index, source in enumerate(ai.source_urls[:5]))
            rows.append(("AI cenové zdroje", links))

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
