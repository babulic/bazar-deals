import json
from decimal import Decimal
from pathlib import Path

import pytest

from bazar_deals.adapters.aukro import AukroHuntClient, _SMALL_CATEGORIES, _listing_from_public_node, _search_body
from bazar_deals.adapters.vinted import VintedHuntClient, VintedProClient, _CATALOGS, _catalog_url
from bazar_deals.domain import Marketplace, Vertical
from bazar_deals.htmlparse import (
    parse_bazos_detail,
    parse_json_ld_products,
    parse_vinted_catalog_payload,
    parse_vinted_detail,
    parse_vinted_items,
)

ROOT = Path(__file__).parent / "fixtures"


def test_aukro_json_ld_fixture() -> None:
    listings = AukroHuntClient(fixture_path=ROOT / "aukro.html").fetch_new()
    assert listings[0].title == "iPhone 13"
    assert listings[0].price.amount == 80
    assert listings[0].marketplace is Marketplace.AUKRO


def test_aukro_hunts_fast_moving_categories_not_raw_newest() -> None:
    assert 100838 in _SMALL_CATEGORIES
    assert 52651 in _SMALL_CATEGORIES
    assert 144281 in _SMALL_CATEGORIES
    assert 144304 in _SMALL_CATEGORIES
    assert 148663 in _SMALL_CATEGORIES
    assert 88109 in _SMALL_CATEGORIES
    body = _search_body(100838)
    assert body["categoryId"] == 100838
    assert "categoryId" not in _search_body(None)


def test_vinted_hunts_fast_moving_catalogs_not_raw_newest() -> None:
    assert "3565-electronics_phones" in _CATALOGS
    assert "4874-hc_trading_cards" in _CATALOGS
    assert "16-footwear" in _CATALOGS
    assert "4-womens" not in _CATALOGS
    assert "5-mens" not in _CATALOGS
    url = _catalog_url("3565-electronics_phones", lo=20, hi=110, page=1)
    assert url.startswith("https://www.vinted.sk/catalog/3565-electronics_phones?")
    assert "order=newest_first" in url
    assert "price_from=20" in url
    assert "price_to=110" in url


def test_aukro_public_backend_buy_now_mapping() -> None:
    listing = _listing_from_public_node(
        {
            "itemId": 7130000001,
            "itemName": "Apple iPhone 13 128GB",
            "startingTime": "2026-08-21T06:00:00+02:00",
            "buyNowActive": True,
            "buyNowPrice": {"amount": 1999, "currency": "CZK"},
            "seoUrl": "apple-iphone-13-128gb",
            "auction": False,
            "adultContent": False,
            "sellerLogin": "seller1",
            "seller": {"positiveFeedbackPercentage": 0.99},
            "location": "Brno",
        }
    )
    assert listing is not None
    assert listing.marketplace is Marketplace.AUKRO
    assert listing.buy_now is True
    assert listing.price.amount == 1999
    assert listing.price.currency == "CZK"
    assert str(listing.url).startswith("https://aukro.sk/apple-iphone-13-128gb-7130000001")


def test_aukro_public_backend_rejects_auction_and_adult() -> None:
    base = {
        "itemId": 1,
        "itemName": "Item",
        "startingTime": "2026-08-21T06:00:00+02:00",
        "buyNowActive": True,
        "buyNowPrice": {"amount": 500, "currency": "CZK"},
        "seoUrl": "item",
        "auction": False,
        "adultContent": False,
    }
    assert _listing_from_public_node({**base, "auction": True}) is None
    assert _listing_from_public_node({**base, "adultContent": True}) is None


def test_json_ld_description_is_kept_for_scoring() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"iPhone SE 2020 64 GB",'
        '"description":"Batéria 77 %, bez krabičky.",'
        '"url":"https://aukro.sk/iphone-se-1",'
        '"offers":{"@type":"Offer","price":"50","priceCurrency":"EUR"}}'
        "</script>"
    )
    listing = parse_json_ld_products(html, marketplace=Marketplace.AUKRO, default_currency="EUR")[0]
    assert "77 %" in listing.description
    assert "bez krabičky" in listing.description


def test_vinted_html_fixture() -> None:
    listings = VintedHuntClient(fixture_path=ROOT / "vinted.html").fetch_new()
    assert listings[0].external_id == "4242"
    assert listings[0].price.amount == 90


def test_vinted_current_nextjs_hydration_catalog() -> None:
    item = {
        "content_source": "catalog",
        "favourite_count": 0,
        "id": 9726128618,
        "item_box": {
            "accessibility_label": "Apple iPhone 13 128 GB, Stav: Veľmi dobrý, 90 €",
            "item_id": 9726128618,
        },
        "price": {"amount": "90.00", "currency_code": "EUR"},
        "service_fee": {"amount": "5.20", "currency_code": "EUR"},
        "title": "Apple iPhone 13 128 GB",
        "total_item_price": {"amount": "95.20", "currency_code": "EUR"},
        "url": "/items/9726128618-apple-iphone-13-128-gb",
        "user": {"id": 42, "login": "seller"},
    }
    hydrated = "7:" + json.dumps([item], ensure_ascii=False, separators=(",", ":"))
    html = f"<script>self.__next_f.push([1,{json.dumps(hydrated)}])</script>"
    listings = parse_vinted_items(html)
    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_id == "9726128618"
    assert listing.title == "Apple iPhone 13 128 GB"
    assert listing.price.amount == 90
    assert listing.seller_id == "seller"
    assert "Veľmi dobrý" in listing.description
    assert "/items/9726128618-apple-iphone-13-128-gb" in str(listing.url)


def test_vinted_detail_extracts_description() -> None:
    html = '{"description":"Batéria 77 %, bez krabičky","status":"Veľmi dobrý"}'
    detail = parse_vinted_detail(html)
    assert "77 %" in detail
    assert "bez krabičky" in detail
    assert "Veľmi dobrý" in detail


def test_vinted_detail_extracts_nextjs_hydration() -> None:
    hydrated = '8:{"description":"Batéria 77 %, bez krabičky","status":"Veľmi dobrý"}'
    html = f"<script>self.__next_f.push([1,{json.dumps(hydrated)}])</script>"
    detail = parse_vinted_detail(html)
    assert "77 %" in detail
    assert "bez krabičky" in detail


def test_bazos_detail_extracts_meta_description() -> None:
    html = '<meta name="description" content="iPhone SE 2020, batéria 77 %, bez krabice">'
    detail = parse_bazos_detail(html)
    assert "77 %" in detail
    assert "bez krabice" in detail


def test_vinted_product_item_hydration_catalog() -> None:
    chunk = {
        "items": [
            {
                "id": 9800847268,
                "productItem": {
                    "id": 9800847268,
                    "title": "Obudowa case etui iPhone 17 Air czarny",
                    "url": "/items/9800847268-obudowa-case-etui-iphone-17-air-czarny",
                    "price": {"amount": "28.16", "currencyCode": "EUR"},
                    "itemBox": {
                        "accessibilityLabel": "Obudowa case etui iPhone 17 Air czarny, 28.16 €",
                    },
                },
            }
        ]
    }
    hydrated = "7:" + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
    html = f"<script>self.__next_f.push([1,{json.dumps(hydrated)}])</script>"
    listings = parse_vinted_items(html)
    assert len(listings) == 1
    assert listings[0].external_id == "9800847268"
    assert listings[0].price.amount == Decimal("28.16")
    assert "9800847268" in str(listings[0].url)


def test_vinted_catalog_api_payload_accepts_absolute_url() -> None:
    payload = {
        "items": [
            {
                "id": 9800847268,
                "title": "Apple iPhone 13 128 GB",
                "price": {"amount": "90.00", "currency_code": "EUR"},
                "url": "https://www.vinted.sk/items/9800847268-apple-iphone-13-128-gb",
                "path": "/items/9800847268-apple-iphone-13-128-gb",
                "user": {"login": "seller"},
                "item_box": {"accessibility_label": "Apple iPhone 13 128 GB, 90 €"},
            }
        ]
    }
    listings = parse_vinted_catalog_payload(payload)
    assert len(listings) == 1
    assert listings[0].title == "Apple iPhone 13 128 GB"
    assert listings[0].price.amount == 90
    assert listings[0].seller_id == "seller"
    assert str(listings[0].url).startswith("https://www.vinted.sk/items/9800847268")


def test_vinted_hunt_loads_catalog_api(monkeypatch) -> None:
    import httpx

    from bazar_deals.adapters import vinted as vinted_mod

    monkeypatch.setattr(vinted_mod, "_CATALOGS", ("3565-electronics_phones",))
    monkeypatch.setattr(vinted_mod, "hunt_fetch_queries", lambda: ())
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    item = {
        "id": 9800847268,
        "title": "Apple iPhone 13 128 GB",
        "price": {"amount": "90.00", "currency_code": "EUR"},
        "url": "https://www.vinted.sk/items/9800847268-apple-iphone-13-128-gb",
        "user": {"login": "seller"},
        "item_box": {"accessibility_label": "Apple iPhone 13 128 GB, 90 €"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/") in {"", "/"}:
            return httpx.Response(200, text="<html>ok</html>")
        if request.url.path == "/api/v2/catalog/items":
            if "catalog_ids=3565" in str(request.url):
                return httpx.Response(200, json={"items": [item]})
            return httpx.Response(200, json={"items": []})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, follow_redirects=True) as client:
        listings = VintedHuntClient(client=client).fetch_new()
    assert len(listings) == 1
    assert listings[0].external_id == "9800847268"


def test_vinted_pro_still_sell_side() -> None:
    with pytest.raises(RuntimeError, match="sell-side"):
        VintedProClient().fetch_new(Vertical.APPLE)


def test_vinted_datadome_is_a_fetch_error(monkeypatch) -> None:
    import httpx

    from bazar_deals.adapters import vinted as vinted_mod

    monkeypatch.setattr(vinted_mod, "_CATALOGS", ("3565-electronics_phones",))
    monkeypatch.setattr(vinted_mod, "hunt_fetch_queries", lambda: ())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html>captcha-delivery blocked</html>",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(RuntimeError, match="DataDome"):
            VintedHuntClient(client=client).fetch_new()


def test_vinted_empty_bot_page_is_a_fetch_error(monkeypatch) -> None:
    import httpx

    from bazar_deals.adapters import vinted as vinted_mod

    monkeypatch.setattr(vinted_mod, "_CATALOGS", ("3565-electronics_phones",))
    monkeypatch.setattr(vinted_mod, "hunt_fetch_queries", lambda: ())

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/v2/catalog/items" in request.url.path:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, text="<html><title>Please wait</title></html>")

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(RuntimeError, match="DataDome"):
            VintedHuntClient(client=client).fetch_new()


def test_vinted_skips_catalog_dump_when_sku_search_is_configured(monkeypatch) -> None:
    import httpx

    from bazar_deals.adapters import vinted as vinted_mod
    from bazar_deals.config import Settings

    monkeypatch.setattr(vinted_mod, "_CATALOGS", ("3565-electronics_phones",))
    monkeypatch.setattr(vinted_mod, "hunt_fetch_queries", lambda: ("iphone se",))
    item = {
        "id": 9800847268,
        "title": "Apple iPhone SE 64 GB",
        "price": {"amount": "90.00", "currency_code": "EUR"},
        "url": "https://www.vinted.sk/items/9800847268-apple-iphone-se-64-gb",
        "user": {"login": "seller"},
        "item_box": {"accessibility_label": "Apple iPhone SE 64 GB, 90 €"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/") in {"", "/"}:
            return httpx.Response(200, text="<html>ok</html>")
        if request.url.path == "/api/v2/catalog/items":
            assert "catalog_ids=" not in str(request.url)
            return httpx.Response(200, json={"items": [item]})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        listings = VintedHuntClient(
            Settings(bazos_request_gap_seconds=0),
            client=client,
        ).fetch_new()
    assert len(listings) == 1
    assert listings[0].external_id == "9800847268"


def test_aukro_skips_category_dump_when_sku_search_is_configured(monkeypatch) -> None:
    import httpx

    from bazar_deals.adapters import aukro as aukro_mod
    from bazar_deals.config import Settings

    monkeypatch.setattr(aukro_mod, "hunt_fetch_queries", lambda: ("iphone se",))
    posts: list[dict] = []

    def fake_post(url, **kwargs):
        posts.append(kwargs)
        body = kwargs.get("json") or {}
        request = httpx.Request("POST", str(url))
        if body.get("text"):
            return httpx.Response(200, json={"content": []}, request=request)
        raise AssertionError("category dump should be skipped")

    monkeypatch.setattr(aukro_mod.httpx, "post", fake_post)
    found = AukroHuntClient(Settings(bazos_request_gap_seconds=0)).fetch_new()
    assert found == []
    assert posts
    assert all((item.get("json") or {}).get("text") for item in posts)
