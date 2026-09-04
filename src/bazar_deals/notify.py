from __future__ import annotations

import re
from decimal import Decimal

from bazar_deals.domain import Action, Deal, Vertical
from bazar_deals.soldcomps import PriceBookMiss

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
        f"očakávaný čistý zisk: {costs.net_profit} €\n"
        f"BUY: {_buy_flag(deal)}\n"
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
        ("BUY", _buy_flag(deal)),
        ("titulok inzerátu", _md_link(title, url)),
        ("identifikovaný tovar", item.canonical_name or "—"),
        ("typ tovaru", kind),
    ]
    if item.brand:
        rows.append(("značka", item.brand))
    if item.model:
        rows.append(("model", item.model))
    if item.vertical is not None:
        rows.append(("vertikála", item.vertical.value))
    if listing.marketplace.value == "ebay":
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").casefold()
        if host.startswith("www."):
            host = host[4:]
        rows.append(("marketplace", host or "ebay"))
    else:
        rows.append(("marketplace", listing.marketplace.value))
    rows.append(("id", listing.external_id))
    rows.append(("nákupná cena", _eur(costs.buy_price)))
    if typical > 0:
        rows.append(("finálna konzervatívna rýchlopredajná cena", _eur(typical)))
        rows.append(("rozdiel od obvyklej ceny", format_price_delta(costs.buy_price, typical)))
    else:
        rows.append(("finálna konzervatívna rýchlopredajná cena", "neznáma"))
        rows.append(("rozdiel od obvyklej ceny", "obvyklá neznáma"))
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
    heading = f"**BUY: {_buy_flag(deal)}** · {_md_link(title, url)} · {kind}"
    body = "\n".join(f"- {key}: {value}" for key, value in rows)
    return f"{heading}\n\n{body}"


def format_price_delta(asking: Decimal, typical: Decimal | None) -> str:
    """Asking minus usual quick-sale price. Negative means cheaper than usual."""
    if typical is None or typical <= 0:
        return "obvyklá neznáma"
    delta = (asking - typical).quantize(Decimal("0.01"))
    if delta == 0:
        return "0 € vs obvyklá"
    if delta < 0:
        return f"{_eur(delta)} vs obvyklá (lacnejší)"
    return f"+{_eur(delta)} vs obvyklá (drahší)"


def format_compact_listing(
    *,
    title: str,
    url: str,
    asking: Decimal,
    typical: Decimal | None,
    extra: str = "",
) -> str:
    parts = [_md_link(title or url, url), f"nákup {_eur(asking)}"]
    if typical is not None and typical > 0:
        parts.append(f"obvyklá {_eur(typical)}")
        parts.append(format_price_delta(asking, typical))
    else:
        parts.append("obvyklá neznáma")
    if extra:
        parts.append(extra)
    return " · ".join(parts)


def is_ai_rejected(deal: Deal) -> bool:
    """True when the displayed price lacks a successful final web review."""
    review = deal.ai_review
    if review is not None and not review.approved:
        return True
    reason = (deal.reason or "").casefold()
    return reason.startswith(
        ("ai rejected", "ai review unavailable", "ai review cap", "ai review time")
    )


def is_cheaper_than_usual(deal: Deal) -> bool:
    """True when asking is below the conservative usual price — a possible near-miss.

    Overpriced ads (šiltovka 20 € vs obvyklá 7 €) are not near-misses.
    A discarded AI typical is not a near-miss either.
    """
    if is_ai_rejected(deal):
        return False
    typical = deal.costs.estimated_resale
    return typical > 0 and deal.costs.buy_price < typical


def keep_price_book_miss(miss: PriceBookMiss) -> bool:
    """Keep unknown usual, or asking below the thin-sample usual. Drop drahší."""
    typical = miss.typical
    if typical is None or typical <= 0:
        return True
    return miss.listing.price.amount < typical


def format_compact_deal(deal: Deal) -> str:
    listing = deal.item.listing
    typical = deal.costs.estimated_resale
    extra = f"čistý zisk {_eur(deal.costs.net_profit)}"
    if deal.action is Action.BUY:
        extra = f"BUY áno · {extra}"
    return format_compact_listing(
        title=listing.title or deal.item.canonical_name,
        url=str(listing.url),
        asking=deal.costs.buy_price,
        typical=typical if typical > 0 else None,
        extra=extra,
    )


def format_price_book_miss(miss: PriceBookMiss) -> str:
    listing = miss.listing
    extra = f"porovnateľné {miss.peer_count}/{miss.required}"
    line = format_compact_listing(
        title=listing.title or miss.query,
        url=str(listing.url),
        asking=listing.price.amount,
        typical=miss.typical,
        extra=extra,
    )
    if not miss.peers:
        return line
    peers = " · ".join(
        f"{_md_link(peer.title or str(peer.url), str(peer.url))} {_eur(peer.price.amount)}"
        for peer in miss.peers[:5]
    )
    return f"{line}\n  - porovnateľné: {peers}"
    mapping = {
        Vertical.RETRO: settings.telegram_chat_retro,
        Vertical.MINERAL: settings.telegram_chat_mineral,
        Vertical.APPLE: settings.telegram_chat_apple,
        Vertical.NETWORK: settings.telegram_chat_network,
    }
    return mapping.get(vertical, "") if vertical else ""


def _buy_flag(deal: Deal) -> str:
    if deal.action is Action.BUY:
        return "áno"
    return "nie"


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
    quantized = Decimal(amount).quantize(Decimal("0.01"))
    if quantized == quantized.to_integral_value():
        return f"{int(quantized)} €"
    return f"{quantized} €"


def _md_link(label: str, url: str) -> str:
    safe = label.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe}]({url})"
