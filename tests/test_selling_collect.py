from decimal import Decimal

import httpx
import pytest

from bazar_deals.config import Settings
from bazar_deals.selling.collect import (
    BAZOS_PAGE_SIZE,
    CollectedListing,
    SourceResult,
    closeness,
    collect_aukro,
    collect_bazos,
    collect_ebay,
    collect_vinted,
    match_listing,
    refresh_inventory,
    score_match,
    similarity,
)
from bazar_deals.selling.inventory import Inventory, InventoryItem, load_inventory

BAZOS_BLOCK = """
<div class="inzeraty inzeratyflex">
<div class="inzeratynadpis"><a href="https://pc.bazos.sk/inzerat/{id}/x.php"><img></a>
<h2 class=nadpis><a href="https://pc.bazos.sk/inzerat/{id}/x.php">{title}</a></h2>
</div>
<div class="inzeratycena"><b><span translate="no">   {price} &euro;</span></b></div>
</div>
"""


def bazos_page(count: int, *, start: int, total: int) -> str:
    blocks = "".join(
        BAZOS_BLOCK.format(id=start + index, title=f"Polozka {start + index}", price=10 + index)
        for index in range(count)
    )
    return f"<html>Zobrazených 1-{count} inzerátov z {total}{blocks}</html>"


REQUEST = httpx.Request("GET", "https://example.test/")


def settings() -> Settings:
    return Settings(bazos_request_gap_seconds=0.0)


def test_bazos_walks_every_offset_until_the_reported_total(monkeypatch) -> None:
    seen: list[int] = []

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):
        offset = int((params or {}).get("crz", 0))
        seen.append(offset)
        start = offset + 1
        remaining = 45 - offset
        return httpx.Response(
            200,
            text=bazos_page(min(BAZOS_PAGE_SIZE, remaining), start=start, total=45),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = collect_bazos("0948244165", settings())

    assert seen == [0, 20, 40]
    assert result.pages == 3
    assert result.count == 45
    assert result.reported_total == 45
    assert result.complete()


def test_bazos_stops_when_a_page_repeats_itself(monkeypatch) -> None:
    # Bazos answers an out-of-range offset with the first page again.
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, text=bazos_page(5, start=1, total=5), request=REQUEST
        ),
    )
    result = collect_bazos("x", settings())
    assert result.count == 5
    assert result.pages == 1


def test_bazos_parses_title_and_price(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, text=bazos_page(1, start=7, total=1), request=REQUEST
        ),
    )
    listing = collect_bazos("x", settings()).listings[0]
    assert listing.external_id == "7"
    assert listing.title == "Polozka 7"
    assert listing.price_eur == Decimal("10")


def test_bazos_reports_failure_when_the_first_page_breaks(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    result = collect_bazos("x", settings())
    assert not result.ok
    assert "offline" in result.reason


def test_aukro_follows_total_pages_and_converts_czk(monkeypatch) -> None:
    pages = {
        0: {
            "page": {"totalElements": 3, "totalPages": 2},
            "content": [
                {
                    "itemId": 1,
                    "itemName": "Kalcit",
                    "buyNowPrice": {"amount": 246.0, "currency": "CZK"},
                    "seoUrl": "kalcit",
                },
                {
                    "itemId": 2,
                    "itemName": "Ametyst",
                    "buyNowPrice": {"amount": 100.0, "currency": "EUR"},
                    "seoUrl": "ametyst",
                },
            ],
        },
        1: {
            "page": {"totalElements": 3, "totalPages": 2},
            "content": [
                {
                    "itemId": 3,
                    "itemName": "Topaz",
                    "auctionPrice": {"amount": 1106.0, "currency": "CZK"},
                    "seoUrl": "topaz",
                }
            ],
        },
    }
    calls: list[int] = []

    def fake_post(url, params=None, headers=None, json=None, timeout=None, follow_redirects=None):
        assert json == {"sellerId": 101485136}
        page = int((params or {}).get("page", 0))
        calls.append(page)
        return httpx.Response(200, json=pages[page], request=REQUEST)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = collect_aukro(101485136, settings())

    assert calls == [0, 1]
    assert result.count == 3
    assert result.complete()
    prices = {item.title: item.price_eur for item in result.listings}
    # aukro.sk prices in whole euros, so the CZK figure is rounded, not carried
    # over with conversion noise.
    assert prices["Kalcit"] == Decimal("10")
    # An EUR price from the shared backend must not be divided again.
    assert prices["Ametyst"] == Decimal("100")
    # Auction-only offers still carry a price worth planning around.
    assert prices["Topaz"] == Decimal("45")


def test_credentialed_sources_are_skipped_not_faked() -> None:
    blank = Settings(
        ebay_client_id="",
        ebay_client_secret="",
        vinted_access_key="",
        vinted_signing_key="",
    )
    ebay = collect_ebay("berg-kristalle", blank)
    vinted = collect_vinted(blank)
    assert not ebay.ok and "EBAY_CLIENT_ID" in ebay.reason
    assert not vinted.ok and "VINTED_ACCESS_KEY" in vinted.reason
    assert ebay.count == 0 and vinted.count == 0


def test_incomplete_source_is_flagged() -> None:
    partial = SourceResult(
        marketplace="bazos", ok=True, pages=1, reported_total=19,
        listings=[CollectedListing(
            marketplace="bazos", external_id="1", title="x", price_eur=Decimal("1")
        )],
    )
    assert not partial.complete()
    assert partial.ok
    assert partial.count == 1


def test_closeness_survives_the_czech_translation() -> None:
    slovak = "Prírodný Jadeit - vybrúsený a vyleštený, na výrobu šperkov"
    czech = "Přírodní jadeit - broušený a leštěný. Pro výrobu šperků."
    # Almost no whole token survives the translation.
    assert similarity(slovak, czech) < 0.25
    assert closeness(slovak, czech) > 0.6


def test_match_hints_separate_identical_variants() -> None:
    items = load_inventory().items
    matched = match_listing(
        CollectedListing(
            marketplace="aukro",
            external_id="1",
            title="Videočip CSG 8565 R2 VIC-II z roku 1993 pro Commodore 64: C64C, C64G",
            price_eur=Decimal("33"),
        ),
        items,
    )
    assert matched.id == "vic2-8565r2-1993"

    new_psu = match_listing(
        CollectedListing(
            marketplace="aukro",
            external_id="2",
            title="Prodám nový spolehlivý zdroj pro každý Commodore 64: C64, C64C, C64G",
            price_eur=Decimal("33"),
        ),
        items,
    )
    assert new_psu.id == "psu-c64-new"


def test_unrelated_listing_stays_unmatched() -> None:
    items = load_inventory().items
    assert (
        match_listing(
            CollectedListing(
                marketplace="bazos",
                external_id="9",
                title="Detská trojkolka ružová s vodiacou tyčou",
                price_eur=Decimal("25"),
            ),
            items,
        )
        is None
    )


def test_hint_penalty_only_applies_to_hinted_items() -> None:
    hinted = InventoryItem(id="a", segment="retro", title="Zdroj C64", match_hints=["1993"])
    plain = InventoryItem(id="b", segment="retro", title="Zdroj C64")
    assert score_match("Zdroj C64", plain) == pytest.approx(1.0)
    assert score_match("Zdroj C64", hinted) == pytest.approx(0.5)
    assert score_match("Zdroj C64 1993", hinted) > 0.75


def test_refresh_keeps_prices_from_sources_that_could_not_be_collected() -> None:
    inventory = Inventory(
        items=[
            InventoryItem(
                id="kalcit",
                segment="minerals",
                title="100g kalcit kryštály zlaťák",
                listed={"bazos": Decimal("9"), "ebay": Decimal("13")},
            )
        ]
    )
    results = [
        SourceResult(
            marketplace="bazos",
            ok=True,
            pages=1,
            reported_total=1,
            listings=[
                CollectedListing(
                    marketplace="bazos",
                    external_id="1",
                    title="100g kalcit kryštály zlaťák",
                    price_eur=Decimal("11"),
                )
            ],
        ),
        SourceResult(marketplace="ebay", ok=False, reason="no credentials"),
    ]

    refreshed, report = refresh_inventory(inventory, results)
    listed = refreshed.items[0].listed
    assert listed["bazos"] == Decimal("11")
    # eBay could not be checked, so its old price survives instead of vanishing.
    assert listed["ebay"] == Decimal("13")
    assert report.matched == 1
    assert refreshed.partial == ["ebay"]


def test_refresh_drops_items_that_left_a_collected_marketplace() -> None:
    inventory = Inventory(
        items=[
            InventoryItem(
                id="sold",
                segment="retro",
                title="Videochip 6569R5 VIC-II",
                listed={"bazos": Decimal("35")},
            )
        ]
    )
    results = [SourceResult(marketplace="bazos", ok=True, pages=1, reported_total=0)]
    refreshed, _ = refresh_inventory(inventory, results)
    assert refreshed.items[0].listed == {}
