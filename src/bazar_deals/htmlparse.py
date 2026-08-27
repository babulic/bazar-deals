from __future__ import annotations

import html as html_lib
import json
import re
from decimal import Decimal, InvalidOperation

from bazar_deals.domain import Listing, Marketplace, Money

_LD_JSON = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_NEXT_CHUNK = re.compile(
    r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)',
    re.S,
)
_VINTED_ITEM = re.compile(
    r'href="(https://www\.vinted\.sk/items/(\d+)[^"]*)"[^>]*>([^<]{3,120})<',
    re.I,
)
_EBAY_ITEM = re.compile(
    r'href="(https://www\.ebay\.de/[^"]*?itm/[^"]+)"[^>]*>\s*<span[^>]*>([^<]{3,160})</span>',
    re.I,
)
_EBAY_CARD = re.compile(
    r'href="(https://www\.ebay\.de/[^"]*?itm/(\d+)[^"]*)"[^>]*>\s*<span[^>]*>([^<]{3,160})</span>'
    r".{0,1200}?s-item__price[^>]*>\s*(?:EUR\s*)?([\d]{1,5}(?:[.,]\d{2})?)",
    re.I | re.S,
)


def parse_json_ld_products(html: str, *, marketplace: Marketplace, default_currency: str) -> list[Listing]:
    listings: list[Listing] = []
    for raw in _LD_JSON.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        listings.extend(_products_from_ld(data, marketplace, default_currency))
    return listings


def _products_from_ld(node: object, marketplace: Marketplace, currency: str) -> list[Listing]:
    found: list[Listing] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_products_from_ld(item, marketplace, currency))
        return found
    if not isinstance(node, dict):
        return found
    types = node.get("@type")
    type_name = types if isinstance(types, str) else " ".join(types or [])
    if "Product" in type_name:
        listing = _product_listing(node, marketplace, currency)
        if listing:
            found.append(listing)
    for value in node.values():
        if isinstance(value, (dict, list)):
            found.extend(_products_from_ld(value, marketplace, currency))
    return found


def _product_listing(node: dict, marketplace: Marketplace, currency: str) -> Listing | None:
    name = (node.get("name") or "").strip()
    url = node.get("url") or node.get("@id")
    if not name or not url:
        return None
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    amount = _decimal(offers.get("price") or node.get("price"))
    offer_currency = offers.get("priceCurrency") or currency
    offer_type = str(offers.get("@type") or "")
    buy_now = "Auction" not in offer_type
    description = _plain_text(str(node.get("description") or ""))
    return Listing(
        marketplace=marketplace,
        external_id=str(url).rstrip("/").split("/")[-1],
        title=name,
        description=description,
        url=str(url),
        price=Money(amount=amount, currency=str(offer_currency)),
        buy_now=buy_now,
    )


def parse_vinted_items(html: str) -> list[Listing]:
    """Parse both old cards and the current public Next.js hydration payload."""
    listings: list[Listing] = []
    seen: set[str] = set()

    hydrated = _next_hydration_text(html)
    if hydrated:
        decoder = json.JSONDecoder()
        for match in re.finditer(r'\{"content_source"\s*:', hydrated):
            try:
                node, _ = decoder.raw_decode(hydrated, match.start())
            except json.JSONDecodeError:
                continue
            if not isinstance(node, dict):
                continue
            listing = _vinted_listing_from_node(node)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            listings.append(listing)

    # Backward-compatible fallback for older/static fixtures.
    json_card = re.compile(
        r'"id":(\d{4,}),"title":"((?:\\.|[^"\\]){3,160})".{0,800}?"amount":"([\d.]+)"',
        re.S,
    )
    for item_id, title, amount in json_card.findall(html):
        if item_id in seen:
            continue
        seen.add(item_id)
        listings.append(
            Listing(
                marketplace=Marketplace.VINTED,
                external_id=item_id,
                title=_decode_json_text(title),
                url=f"https://www.vinted.sk/items/{item_id}",
                price=Money(amount=_decimal(amount), currency="EUR"),
            )
        )
    for url, item_id, title in _VINTED_ITEM.findall(html):
        if item_id in seen:
            continue
        seen.add(item_id)
        listings.append(
            Listing(
                marketplace=Marketplace.VINTED,
                external_id=item_id,
                title=_plain_text(title),
                url=url.split("?")[0],
                price=Money(amount=Decimal("0"), currency="EUR"),
            )
        )
    return listings


def _vinted_listing_from_node(node: dict) -> Listing | None:
    item_id = str(node.get("id") or "")
    title = str(node.get("title") or "").strip()
    price = node.get("price") or {}
    url = str(node.get("url") or "")
    if not item_id.isdigit() or not title or not isinstance(price, dict) or not url.startswith("/items/"):
        return None
    amount = _decimal(price.get("amount"))
    if amount <= 0:
        return None
    currency = str(price.get("currency_code") or "EUR")
    item_box = node.get("item_box") if isinstance(node.get("item_box"), dict) else {}
    description = _plain_text(
        str(item_box.get("accessibility_label") or item_box.get("second_line") or "")
    )
    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    brand = ""
    raw_brand = node.get("brand")
    if isinstance(raw_brand, dict):
        brand = str(raw_brand.get("title") or raw_brand.get("name") or "")
    brand = brand or str(node.get("brand_title") or "")
    size = str(item_box.get("size_title") or node.get("size_title") or "")
    return Listing(
        marketplace=Marketplace.VINTED,
        external_id=item_id,
        title=title,
        description=description,
        url=f"https://www.vinted.sk{url.split('?')[0]}",
        price=Money(amount=amount, currency=currency),
        seller_id=str(user.get("login") or "") or None,
        raw={
            "service_fee": node.get("service_fee"),
            "total_item_price": node.get("total_item_price"),
            "content_source": node.get("content_source"),
            "brand": brand or None,
            "size": size or None,
        },
    )


def parse_vinted_detail(html: str) -> str:
    """Best-effort public detail extraction; no private API/challenge bypass."""
    source = _next_hydration_text(html) or html
    description = _json_string(source, "description")
    status = _json_string(source, "status") or _json_string(source, "condition")
    brand = _json_string(source, "brand_title")
    parts = [
        part
        for part in (_plain_text(description), _plain_text(status), _plain_text(brand))
        if part
    ]
    return " ".join(parts).strip()


def _next_hydration_text(html: str) -> str:
    chunks: list[str] = []
    for raw_string in _NEXT_CHUNK.findall(html):
        try:
            decoded = json.loads(raw_string)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, str):
            chunks.append(decoded)
    return "\n".join(chunks)


def parse_bazos_detail(html: str) -> str:
    """Extract public Bazoš detail text from the visible detail/meta page."""
    candidates: list[str] = []
    for pattern in (
        r'<div[^>]+class="[^"]*popisdetail[^"]*"[^>]*>(.*?)</div>',
        r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
    ):
        match = re.search(pattern, html, flags=re.I | re.S)
        if match:
            text = _plain_text(match.group(1))
            if text:
                candidates.append(text)
    return max(candidates, key=len) if candidates else ""


def parse_ebay_html(html: str) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for url, item_id, title, price in _EBAY_CARD.findall(html):
        if url in seen or "Shop on eBay" in title or "Shop auf eBay" in title:
            continue
        seen.add(url)
        listings.append(
            Listing(
                marketplace=Marketplace.EBAY,
                external_id=item_id,
                title=title.strip(),
                url=url.split("?")[0],
                price=Money(amount=_decimal(price), currency="EUR"),
            )
        )
    for url, title in _EBAY_ITEM.findall(html):
        if url in seen or "Shop on eBay" in title or "Shop auf eBay" in title:
            continue
        seen.add(url)
        listings.append(
            Listing(
                marketplace=Marketplace.EBAY,
                external_id=url.rstrip("/").split("/")[-1].split("?")[0],
                title=title.strip(),
                url=url.split("?")[0],
                price=Money(amount=Decimal("0"), currency="EUR"),
            )
        )
    return listings


def _json_string(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*("(?:\\.|[^"\\])*")', text, flags=re.S)
    if not match:
        return ""
    try:
        return str(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return ""


def _decode_json_text(text: str) -> str:
    try:
        return str(json.loads(f'"{text}"'))
    except json.JSONDecodeError:
        return text.replace('\\"', '"')


def _plain_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html_lib.unescape(text)
    return " ".join(text.split())


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")
