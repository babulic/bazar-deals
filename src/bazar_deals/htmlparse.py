from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from bazar_deals.domain import Listing, Marketplace, Money

_LD_JSON = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_VINTED_ITEM = re.compile(
    r'href="(https://www\.vinted\.sk/items/(\d+)[^"]*)"[^>]*>([^<]{3,120})<',
    re.I,
)
_EBAY_ITEM = re.compile(
    r'href="(https://www\.ebay\.de/[^"]+/itm/[^"]+)"[^>]*>\s*<span[^>]*>([^<]{3,160})</span>',
    re.I,
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
    return Listing(
        marketplace=marketplace,
        external_id=str(url).rstrip("/").split("/")[-1],
        title=name,
        url=str(url),
        price=Money(amount=amount, currency=str(offer_currency)),
    )


def parse_vinted_items(html: str) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
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
                title=title.replace('\\"', '"'),
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
                title=title.strip(),
                url=url.split("?")[0],
                price=Money(amount=Decimal("0"), currency="EUR"),
            )
        )
    return listings


def parse_ebay_html(html: str) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for url, title in _EBAY_ITEM.findall(html):
        if url in seen or "Shop on eBay" in title:
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


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")
