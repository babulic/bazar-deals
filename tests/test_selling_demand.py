import json
from decimal import Decimal

import httpx

from bazar_deals.config import Settings
from bazar_deals.github_alerts import GitHubIssueAlerts, SELL_ALERT_LABEL, SELL_ALERT_ISSUE_TITLE
from bazar_deals.selling.demand import (
    BuyerDigest,
    DemandMatch,
    WantAd,
    best_item,
    find_buyers,
    format_buyer_digest,
    is_want_to_buy,
    match_want,
    queries_for,
    searched_sites,
)
from bazar_deals.selling.inventory import Inventory, InventoryItem


def chip() -> InventoryItem:
    return InventoryItem(
        id="cpu-6510",
        segment="retro",
        title="Commodore MOS 6510 CBM: procesor pre všetky varianty C64",
        part_numbers=["6510"],
        keywords=["MOS", "CBM"],
        listed={"aukro": Decimal("25"), "ebay": Decimal("25"), "vinted": Decimal("25")},
    )


def crystal() -> InventoryItem:
    return InventoryItem(
        id="amethyst-namibia-74mm",
        segment="minerals",
        title="74mm lesklý priehľadný fantómový kryštál Ametystu, Namíbia",
        species=["ametyst"],
        origin="namíbia",
        locality="Goboboseb, Brandberg",
        listed={"aukro": Decimal("110"), "bazos": Decimal("110")},
    )


def test_want_to_buy_requires_the_buyers_own_ad() -> None:
    assert is_want_to_buy("Kúpim MOS 6510")
    assert is_want_to_buy("Koupím Commodore AMIGA 600")
    assert is_want_to_buy("Suche Amethyst Brandberg")
    assert is_want_to_buy("Szukam MOS 6510")
    assert is_want_to_buy("Cherche améthyste Brandberg")
    assert is_want_to_buy("Cerco MOS 6510")
    assert is_want_to_buy("Keresek ametiszt Brandberg")
    assert is_want_to_buy("Zoek MOS 6510")
    assert is_want_to_buy("Kupię Commodore 6510")
    assert is_want_to_buy("⚠️Kupim MOS 6510")
    assert not is_want_to_buy("Predám MOS 6510")
    assert not is_want_to_buy("COMMODORE C64 - koupí se zdrojem ZDARMA")
    assert not is_want_to_buy("Inga koupit opál 1,4x4cm")
    assert not is_want_to_buy("SSD Samsung KOUPENO 19.1.2025")
    assert not is_want_to_buy("Koupím já si koně")


def test_queries_prefer_part_numbers_and_locality() -> None:
    assert "6510" in queries_for(chip())
    queries = queries_for(crystal())
    assert any("ametyst" in query.casefold() for query in queries)


def test_numeric_part_needs_product_context() -> None:
    assert match_want("Koupím 6510", chip()) >= 0.8
    assert match_want("SUCHE John Deere 6510", chip()) < 0.5
    assert best_item("SUCHE John Deere 6510", [chip(), crystal()]) is None


def test_searched_sites_cover_central_and_western_europe() -> None:
    sites = searched_sites()
    for host in (
        "bazos.sk",
        "bazos.cz",
        "aukro.cz",
        "vinted.sk",
        "vinted.pl",
        "vinted.de",
        "vinted.fr",
        "kleinanzeigen.de",
        "willhaben.at",
        "ebay.de",
        "ebay.at",
        "ebay.fr",
        "ebay.it",
        "ebay.pl",
        "ebay.nl",
    ):
        assert host in sites


def test_postcard_part_number_does_not_match_chip() -> None:
    assert match_want("Koupím pohlednici A6510", chip()) < 0.5
    assert best_item("Koupím pohlednici A6510", [chip(), crystal()]) is None


def test_part_number_want_ad_matches_own_chip() -> None:
    item = chip()
    assert match_want("Koupím MOS 6510 na C64", item) >= 0.8
    hit = best_item("Koupím MOS 6510 na C64", [item, crystal()])
    assert hit is not None
    matched, score = hit
    assert matched.id == "cpu-6510"
    assert score >= 0.8


def test_mineral_want_ad_needs_species_and_place() -> None:
    assert match_want("Kúpim ametyst z Namíbie Brandberg", crystal()) >= 0.8
    assert best_item("Kúpim prevodovku DSG", [chip(), crystal()]) is None


def test_digest_pairs_buyer_with_own_listings() -> None:
    item = chip()
    digest = BuyerDigest(
        matches=[
            DemandMatch(
                want=WantAd(
                    marketplace="bazos",
                    site="bazos.cz",
                    external_id="111",
                    title="Koupím MOS 6510",
                    url="https://pc.bazos.cz/inzerat/111/",
                    offer_eur=Decimal("20"),
                ),
                item=item,
                score=0.82,
            )
        ],
        notes=["bazos.cz: fetched 1"],
    )
    body = format_buyer_digest(digest, mention="babulic")
    assert body.startswith("@babulic\n")
    assert "<!-- want:bazos.cz:111:cpu-6510 -->" in body
    assert "**1 kupec/kupci**" in body
    assert "Koupím MOS 6510" in body
    assert "chce kúpiť za:** 20 €" in body or "chce kúpiť za: 20 €" in body
    assert "aukro 25 €" in body
    assert "[bazos.cz](https://pc.bazos.cz/inzerat/111/)" in body
    assert "`cpu-6510`" in body


def test_empty_digest_does_not_ping() -> None:
    body = format_buyer_digest(BuyerDigest(notes=["aukro: fetched 0"]), mention="babulic")
    assert not body.startswith("@babulic")
    assert "**0 kupcov**" in body


def test_cli_buyers_prints_digest(monkeypatch, capsys) -> None:
    from bazar_deals.cli import main
    from bazar_deals.selling import demand as demand_mod

    item = chip()

    def fake_find(inventory, settings, client=None):
        return BuyerDigest(
            matches=[
                DemandMatch(
                    want=WantAd(
                        marketplace="aukro",
                        site="aukro.cz",
                        external_id="9",
                        title="Koupím 6510",
                        url="https://aukro.cz/9",
                        offer_eur=None,
                    ),
                    item=item,
                    score=0.82,
                )
            ],
            notes=["aukro: fetched 1"],
        )

    monkeypatch.setattr(demand_mod, "find_buyers", fake_find)
    monkeypatch.setattr("bazar_deals.cli.find_buyers", fake_find)
    assert main(["sell", "--buyers"]) == 0
    out = capsys.readouterr().out
    assert "Koupím 6510" in out
    assert "neuvedené" in out


def test_sell_buyer_alerts_use_a_separate_issue() -> None:
    settings = Settings(github_token="t", github_repository="babulic/bazar-deals")
    alerts = GitHubIssueAlerts.for_sell_buyers(settings)
    assert alerts._issue_label == SELL_ALERT_LABEL == "bazar-sell"
    assert alerts._issue_title == SELL_ALERT_ISSUE_TITLE
    assert alerts._issue_number == 0


def _bazos_card(url: str, title: str, price: str | None = None) -> str:
    price_html = ""
    if price is not None:
        price_html = f'<div class="inzeratycena"><b><span>{price}</span></b></div>'
    return (
        f'<div class="inzeraty inzeratyflex"><h2 class=nadpis>'
        f'<a href="{url}">{title}</a></h2>{price_html}</div>'
    )


def _kleinanzeigen_card(title: str, price: str) -> str:
    return (
        '<article class="aditem" data-adid="3479" '
        'data-href="/s-anzeige/suche-mos-6510/3479-1-1">'
        f'<h2 class="text-module-begin"><a class="ellipsis" href="/s-anzeige/x/3479-1-1">'
        f"{title}</a></h2>"
        f'<p class="aditem-main--middle--price-shipping--price">{price} €</p>'
        "</article>"
    )


def _willhaben_page(title: str, price: str) -> str:
    payload = {
        "props": {
            "pageProps": {
                "searchResult": {
                    "advertSummaryList": {
                        "advertSummary": [
                            {
                                "id": "1622",
                                "description": title,
                                "attributes": {
                                    "attribute": [
                                        {"name": "HEADING", "values": [title]},
                                        {"name": "PRICE", "values": [price]},
                                        {
                                            "name": "SEO_URL",
                                            "values": [
                                                "kaufen-und-verkaufen/d/suche-mos-6510-1622/"
                                            ],
                                        },
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        }
    }
    return (
        "<html><script id=\"__NEXT_DATA__\" type=\"application/json\">"
        + json.dumps(payload)
        + "</script></html>"
    )


def test_find_buyers_pairs_wtb_ads_and_drops_sell_ads() -> None:
    sk_html = _bazos_card("https://pc.bazos.sk/inzerat/11/mos/", "Kúpim MOS 6510", "18 €")
    cz_html = (
        _bazos_card("https://pc.bazos.cz/inzerat/22/mos/", "Koupím MOS 6510", "490 Kč")
        + _bazos_card("https://pc.bazos.cz/inzerat/23/mos/", "Prodám MOS 6510", "25 Kč")
        + _bazos_card("https://pc.bazos.cz/inzerat/24/dsg/", "Koupím prevodovku DSG")
    )
    aukro = {
        "content": [
            {
                "itemName": "Koupím MOS 6510",
                "itemId": "99",
                "seoUrl": "koupim-mos-6510",
                "buyNowPrice": {"amount": 0, "currency": "CZK"},
            },
            {
                "itemName": "Prodám MOS 6510",
                "itemId": "100",
                "seoUrl": "prodam-mos-6510",
                "buyNowPrice": {"amount": 25, "currency": "EUR"},
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "bazos.sk" in url:
            return httpx.Response(200, text=sk_html)
        if "bazos.cz" in url:
            return httpx.Response(200, text=cz_html)
        if "searchItemsCommon" in url:
            return httpx.Response(200, json=aukro)
        if "vinted." in url:
            return httpx.Response(200, text="<html></html>")
        if "kleinanzeigen.de" in url:
            return httpx.Response(200, text=_kleinanzeigen_card("Suche MOS 6510", "40"))
        if "willhaben.at" in url:
            return httpx.Response(200, text=_willhaben_page("Suche MOS 6510", "35"))
        return httpx.Response(404, json={"message": url})

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    inventory = Inventory(items=[chip(), crystal()])
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        digest = find_buyers(inventory, settings, client=client)

    sites = {row.want.site for row in digest.matches}
    titles = {row.want.title for row in digest.matches}
    assert sites >= {"bazos.sk", "bazos.cz", "aukro.cz", "kleinanzeigen.de", "willhaben.at"}
    assert "Kúpim MOS 6510" in titles
    assert "Koupím MOS 6510" in titles
    assert all(row.item.id == "cpu-6510" for row in digest.matches)
    assert "Prodám MOS 6510" not in titles
    assert "Koupím prevodovku DSG" not in titles
    assert any(row.want.offer_eur == Decimal("18") for row in digest.matches)
    assert any("ebay.de" in note and "EBAY_CLIENT" in note for note in digest.notes)
    body = format_buyer_digest(digest)
    assert "`cpu-6510`" in body
    assert "aukro 25 €" in body
    assert "[aukro.cz](https://aukro.sk/koupim-mos-6510-99)" in body


def test_post_buyer_digest_goes_to_sell_issue() -> None:
    posts: list[str] = []
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/labels/" in path and request.method == "GET":
            return httpx.Response(200, json={"name": "bazar-sell"})
        if request.method == "PATCH" and "/issues/" in path:
            return httpx.Response(200, json={"number": 12})
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/issues"):
            created.append(json.loads(request.content))
            return httpx.Response(201, json={"number": 12})
        if request.method == "POST" and path.endswith("/comments"):
            posts.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={"message": path})

    settings = Settings(github_token="t", github_repository="babulic/bazar-deals")
    body = format_buyer_digest(
        BuyerDigest(
            matches=[
                DemandMatch(
                    want=WantAd(
                        marketplace="bazos",
                        site="bazos.sk",
                        external_id="11",
                        title="Kúpim MOS 6510",
                        url="https://pc.bazos.sk/inzerat/11/",
                        offer_eur=Decimal("18"),
                    ),
                    item=chip(),
                    score=0.82,
                )
            ]
        )
    )
    with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)) as client:
        alerts = GitHubIssueAlerts.for_sell_buyers(settings, client=client)
        assert alerts.post_buyer_digest(body) == 1
    assert created[0]["title"] == "Sell buyers"
    assert created[0]["labels"] == ["bazar-sell"]
    assert posts[0] == body
    assert "Kúpim MOS 6510" in posts[0]
    assert "18 €" in posts[0]
