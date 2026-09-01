import json
from decimal import Decimal

import httpx

from bazar_deals.config import Settings
from bazar_deals.github_alerts import GitHubIssueAlerts, SELL_ALERT_LABEL, SELL_ALERT_ISSUE_TITLE
from bazar_deals.selling.demand import (
    BUY_VERBS,
    BuyerDigest,
    DemandMatch,
    WantAd,
    best_item,
    find_buyers,
    format_buyer_digest,
    is_want_to_buy,
    match_want,
    queries_for,
    searched_buy_phrases,
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


def apatite() -> InventoryItem:
    return InventoryItem(
        id="apatit-durango",
        segment="minerals",
        title="Priehľadný žltý neporušený kryštál apatitu z Mexika",
        species=["apatit"],
        origin="mexiko",
        locality="Cerro del Mercado, Durango",
        listed={"aukro": Decimal("13"), "ebay": Decimal("14")},
    )


def psu() -> InventoryItem:
    return InventoryItem(
        id="psu-1541",
        segment="retro",
        title="Nový zdroj na disketovú jednotku Commodore 1541 II, 1581, 1571",
        part_numbers=["1541", "1581", "1571", "1570"],
        listed={"aukro": Decimal("23"), "bazos": Decimal("22")},
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
    assert is_want_to_buy("Veszek ametiszt Brandberg")
    assert is_want_to_buy("Compro MOS 6510")
    assert is_want_to_buy("J'achète MOS 6510")
    assert is_want_to_buy("Achète améthyste Brandberg")
    assert is_want_to_buy("Ik koop MOS 6510")
    assert is_want_to_buy("Koop MOS 6510")
    assert is_want_to_buy("Kaufe MOS 6510")
    assert is_want_to_buy("⚠️Kupim MOS 6510")
    assert is_want_to_buy("Gesucht MOS 6510")
    assert not is_want_to_buy("Biete MOS 6510")
    assert not is_want_to_buy("Tausche MOS 6510")
    assert not is_want_to_buy("Te koop MOS 6510")
    assert not is_want_to_buy("Predám MOS 6510")
    assert not is_want_to_buy("COMMODORE C64 - koupí se zdrojem ZDARMA")
    assert not is_want_to_buy("Inga koupit opál 1,4x4cm")
    assert not is_want_to_buy("SSD Samsung KOUPENO 19.1.2025")
    assert not is_want_to_buy("Koupím já si koně")


def test_queries_prefer_part_numbers_and_locality() -> None:
    assert "6510" in queries_for(chip())
    queries = queries_for(crystal())
    assert queries[0].casefold() == "ametyst"
    assert any("brandberg" in query.casefold() for query in queries)


def test_title_fallback_keeps_slovak_letters() -> None:
    item = InventoryItem(
        id="safepal",
        segment="other",
        title="SafePal hardvérová kryptomenová peňaženka",
        listed={"bazos": Decimal("40")},
    )
    queries = queries_for(item)
    assert queries[0] == "SafePal"
    blob = " ".join(queries)
    assert "hardvérová" in blob
    assert "kryptomenová" in blob
    assert "hardv " not in blob + " "
    assert "kryptomenov " not in blob + " "


def test_stock_first_searches_put_sku_before_bare_verb() -> None:
    from bazar_deals.selling.demand import _stock_first_searches

    planned = _stock_first_searches(("kúpim", "hľadám"), ["ametyst", "6510"])
    assert planned[0] == "kúpim ametyst"
    assert planned[1] == "hľadám ametyst"
    assert "kúpim 6510" in planned
    assert "kúpim" not in planned


def test_numeric_part_needs_product_context() -> None:
    assert match_want("Koupím 6510", chip()) >= 0.8
    assert match_want("SUCHE John Deere 6510", chip()) < 0.5
    assert best_item("SUCHE John Deere 6510", [chip(), crystal()]) is None


def test_psu_1541_does_not_match_a_disk_drive_sale() -> None:
    drive = "Commodore 1541-II Diskettenlaufwerk"
    assert match_want(drive, psu()) < 0.5
    assert best_item(drive, [psu(), chip()]) is None
    want = "Koupím zdroj 1541"
    assert is_want_to_buy(want)
    assert match_want(want, psu()) >= 0.8
    hit = best_item(want, [psu(), chip()])
    assert hit is not None
    assert hit[0].id == "psu-1541"


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
        "delcampe.net",
        "forum64.de",
        "sbazar.cz",
        "ebay.de",
        "ebay.at",
        "ebay.pl",
    ):
        assert host in sites
    assert "facebook.com" in sites
    assert "olx.pl" in sites
    assert "allegro.pl" not in sites
    assert "ebay.fr" not in sites
    assert "ebay.it" not in sites
    assert "ebay.nl" not in sites
    assert "ebay.es" not in sites
    assert "ebay.be" not in sites


def test_buy_verbs_are_searched_in_pl_hu_it_fr_nl() -> None:
    phrases = searched_buy_phrases()
    for verb in ("kupię", "veszek", "compro", "achète", "koop", "kaufe", "Gesucht"):
        assert verb in phrases
    assert BUY_VERBS[-5:] == ("kupię", "veszek", "compro", "achète", "koop")


def test_research_mode_adds_glossary_aliases_for_stock() -> None:
    from bazar_deals.selling.demand import queries_for

    base = queries_for(crystal())
    extra = queries_for(crystal(), research=True)
    assert extra
    assert len(extra) >= len(base)
    assert any("amethyst" in query.casefold() or "ametyst" in query.casefold() for query in extra)


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
    assert match_want("Kúpim ametyst", crystal()) >= 0.5
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
    assert "Servery: (žiadne)" in body
    assert "facebook.com" not in body


def test_cli_buyers_prints_digest(monkeypatch, capsys) -> None:
    from bazar_deals.cli import main
    from bazar_deals.selling import demand as demand_mod

    item = chip()

    def fake_find(inventory, settings, client=None, **kwargs):
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


def test_zero_buyers_runs_in_process_sell_research_loop(monkeypatch, capsys) -> None:
    from bazar_deals.cli import main
    from bazar_deals.selling import demand as demand_mod

    calls: list[bool] = []

    def fake_find(inventory, settings, client=None, **kwargs):
        calls.append(bool(kwargs.get("research")))
        return BuyerDigest(notes=["aukro: fetched 0"])

    monkeypatch.setattr(demand_mod, "find_buyers", fake_find)
    monkeypatch.setattr("bazar_deals.cli.find_buyers", fake_find)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )
    assert main(["sell", "--buyers"]) == 0
    assert calls == [False, True]
    assert "research loop after 0 buyers or throttled eBay" in capsys.readouterr().out


def test_sell_research_flag_and_offline_do_not_recurse(monkeypatch) -> None:
    from bazar_deals.cli import main
    from bazar_deals.selling import demand as demand_mod

    calls: list[str] = []

    def fake_find(inventory, settings, client=None, **kwargs):
        calls.append("research" if kwargs.get("research") else "first")
        return BuyerDigest(notes=["aukro: fetched 0"])

    monkeypatch.setattr(demand_mod, "find_buyers", fake_find)
    monkeypatch.setattr("bazar_deals.cli.find_buyers", fake_find)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )
    assert main(["sell", "--buyers", "--research"]) == 0
    assert calls == ["research"]
    calls.clear()
    assert main(["sell", "--buyers", "--offline"]) == 0
    assert calls == ["first"]


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


def _willhaben_page(
    title: str,
    price: str,
    seo: str = "kaufen-und-verkaufen/d/suche-mos-6510-1622/",
) -> str:
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
                                        {"name": "SEO_URL", "values": [seo]},
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
    sk_html = (
        _bazos_card("https://pc.bazos.sk/inzerat/11/mos/", "Kúpim MOS 6510", "18 €")
        + _bazos_card("https://pc.bazos.sk/inzerat/12/ame/", "Kúpim ametyst", "40 €")
    )
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
        if "delcampe.net" in url or "forum64.de" in url:
            return httpx.Response(200, text="<html></html>")
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
    assert "Kúpim ametyst" in titles
    assert {row.item.id for row in digest.matches} >= {"cpu-6510", "amethyst-namibia-74mm"}
    assert all(row.item.id in {"cpu-6510", "amethyst-namibia-74mm"} for row in digest.matches)
    assert "Prodám MOS 6510" not in titles
    assert "Koupím prevodovku DSG" not in titles
    assert any(row.want.offer_eur == Decimal("18") for row in digest.matches)
    assert any("ebay.de" in note and "EBAY_CLIENT" in note for note in digest.notes)
    body = format_buyer_digest(digest)
    assert "`cpu-6510`" in body
    assert "aukro 25 €" in body
    assert "[aukro.cz](https://aukro.sk/koupim-mos-6510-99)" in body
    assert "for 'kaufe'" not in body
    assert "for 'suche'" not in body
    assert "developer.mozilla.org" not in body
    ka_notes = [note for note in digest.notes if note.startswith("kleinanzeigen.de")]
    assert len(ka_notes) == 1
    assert "want-ads" in ka_notes[0]


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
        assert alerts.post_buyer_digest("**0 kupcov** na tvoj tovar.", has_buyers=False) == 0
    assert created[0]["title"] == "Sell buyers"
    assert created[0]["labels"] == ["bazar-sell"]
    assert posts[0] == body
    assert "Kúpim MOS 6510" in posts[0]
    assert "18 €" in posts[0]


def test_ebay_credentials_are_stripped() -> None:
    settings = Settings(ebay_client_id='  "app-id"  ', ebay_client_secret="  'cert' \n")
    assert settings.ebay_client_id == "app-id"
    assert settings.ebay_client_secret == "cert"


def test_targeted_queries_cover_late_stock_and_interleave_segments():
    from bazar_deals.selling.demand import _unique_queries
    minerals = [crystal().model_copy(update={"id": f"mineral-{n}", "locality": f"locality-{n}"}) for n in range(20)]
    queries = _unique_queries([*minerals, chip()])
    assert "6510" in queries[:3]
    assert "ametyst" in queries
    assert any(query.startswith("ametyst locality-") for query in queries)


def _quiet_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "oauth2/token" in url:
        return httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "client authentication failed",
            },
        )
    if "searchItemsCommon" in url:
        return httpx.Response(200, json={"content": []})
    if "willhaben.at" in url:
        return httpx.Response(200, text="<html></html>")
    if any(
        host in url
        for host in (
            "bazos.",
            "vinted.",
            "kleinanzeigen.de",
            "ebay.com",
            "delcampe.net",
            "forum64.de",
            "olx.pl",
            "sbazar.cz",
            "facebook.com",
        )
    ):
        if "olx.pl" in url:
            return httpx.Response(
                200,
                text=(
                    "<html>Nie znaleziono ogłoszeń"
                    '<script type="application/ld+json">'
                    '{"@type":"ItemList","numberOfItems":0,"itemListElement":[]}'
                    "</script></html>"
                ),
            )
        return httpx.Response(200, text="<html></html>")
    return httpx.Response(404, json={"message": url})


def test_willhaben_stock_hits_include_links_even_when_not_wtb() -> None:
    seo = "kaufen-und-verkaufen/d/apatit-aus-mexiko-durango-1565218359/"
    html = _willhaben_page("Apatit aus Mexiko - Durango", "14", seo=seo)

    def handler(request: httpx.Request) -> httpx.Response:
        if "willhaben.at" in str(request.url):
            return httpx.Response(200, text=html)
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        digest = find_buyers(Inventory(items=[apatite()]), settings, client=client)

    assert digest.matches == []
    assert digest.near_misses == []
    body = format_buyer_digest(digest)
    assert "**0 kupcov**" in body
    assert "Apatit aus Mexiko" not in body
    assert "názov nie je dopyt kúpim" not in body
    assert "facebook.com" not in body
    assert "allegro.pl" not in body
    assert any("0 want-ads" in note and "willhaben.at" in note for note in digest.notes)
    assert not any("for 'Suche" in note or "for 'Kaufe" in note for note in digest.notes)
    will_notes = [note for note in digest.notes if note.startswith("willhaben.at")]
    assert len(will_notes) == 1


def test_ebay_oauth_401_is_a_single_actionable_note() -> None:
    settings = Settings(ebay_client_id="app-id", ebay_client_secret="cert", ebay_retention_enabled=True)
    transport = httpx.MockTransport(_quiet_handler)
    with httpx.Client(transport=transport) as client:
        digest = find_buyers(Inventory(items=[chip()]), settings, client=client)

    ebay_notes = [note for note in digest.notes if note.startswith("ebay.")]
    assert len(ebay_notes) == 1
    assert "client authentication failed" in ebay_notes[0]
    assert ebay_notes[0].count("ebay.") == 1
    assert "Cert ID" in ebay_notes[0]


def _delcampe_card(title: str, item_id: str, price: str = "12,00 €") -> str:
    return (
        f'<div id="item-{item_id}" data-watch-item>'
        f'<a href="/en_GB/collectables/minerals-fossils/minerals/slug-{item_id}.html" '
        f'class="item-link"><h2 class="item-title">{title}</h2></a>'
        f'<strong class="item-price">{price}</strong></div>'
    )


def _forum64_page(*threads: tuple[str, str]) -> str:
    items = "".join(
        f'<a href="https://www.forum64.de/index.php?thread/{thread_id}-slug/" '
        f'class="messageGroupLink">{title}</a>'
        for title, thread_id in threads
    )
    return f"<ol class='tabularList'>{items}</ol>"


def test_delcampe_wtb_matches_mineral_and_sell_hit_keeps_link() -> None:
    html = _delcampe_card("Suche Amethyst Brandberg", "111") + _delcampe_card(
        "Amethyst Brandberg Namibia crystal", "222", "US$9.00"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "delcampe.net" in str(request.url):
            return httpx.Response(200, text=html)
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[crystal()]), settings, client=client)

    titles = {row.want.title for row in digest.matches}
    assert "Suche Amethyst Brandberg" in titles
    assert all(row.want.site == "delcampe.net" for row in digest.matches)
    assert any(row.want.offer_eur == Decimal("12") for row in digest.matches)
    assert digest.near_misses == []
    body = format_buyer_digest(digest)
    assert "slug-111.html" in body
    assert "slug-222.html" not in body
    assert "názov nie je dopyt kúpim" not in body


def test_forum64_suche_matches_chip_and_biete_is_not_a_buyer() -> None:
    html = _forum64_page(
        ("Suche MOS 6510", "77"),
        ("Biete MOS 6510", "88"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "forum64.de" in str(request.url):
            return httpx.Response(200, text=html)
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[chip()]), settings, client=client)

    titles = {row.want.title for row in digest.matches}
    assert titles == {"Suche MOS 6510"}
    assert digest.matches[0].want.url.endswith("thread/77-slug/")
    assert digest.near_misses == []
    body = format_buyer_digest(digest)
    assert "[forum64.de](https://www.forum64.de/index.php?thread/77-slug/)" in body
    assert "thread/88-slug/" not in body


def test_forum64_cloudflare_block_is_reported_once() -> None:
    calls = {"n": 0}
    challenge = (
        "<html><title>Just a moment...</title>"
        "<div id='challenge-platform'>Cloudflare</div></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "forum64.de" in str(request.url):
            calls["n"] += 1
            return httpx.Response(403, text=challenge)
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[chip()]), settings, client=client)

    forum_notes = [note for note in digest.notes if note.startswith("forum64.de")]
    assert calls["n"] == 1
    assert len(forum_notes) == 1
    assert "Cloudflare" in forum_notes[0]


def test_kleinanzeigen_403_stops_and_does_not_dump_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "kleinanzeigen.de" in str(request.url):
            calls["n"] += 1
            return httpx.Response(403, text="Forbidden")
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[chip(), crystal(), apatite()]), settings, client=client)

    assert calls["n"] == 1
    ka_notes = [note for note in digest.notes if note.startswith("kleinanzeigen.de")]
    assert len(ka_notes) == 1
    assert "HTTP 403" in ka_notes[0]
    assert "stopped after" in ka_notes[0]
    blob = "\n".join(digest.notes)
    assert "for 'kaufe'" not in blob
    assert "developer.mozilla.org" not in blob
    body = format_buyer_digest(digest)
    assert "for 'kaufe'" not in body
    assert "HTTP 403" in body


def test_bazos_stops_after_429_on_the_first_stock_query() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "bazos.sk" in url:
            calls.append(str(request.url.params.get("hledat") or ""))
            return httpx.Response(429, text="slow down")
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[crystal()]), settings, client=client)

    assert calls
    assert calls[0].casefold().startswith("kúpim ametyst") or calls[0].startswith("kúpim ametyst")
    assert len(calls) == 1
    assert any("bazos.sk" in note and "429" in note for note in digest.notes)


def strap() -> InventoryItem:
    return InventoryItem(
        id="watch-strap",
        segment="commodity",
        title="Silikónový remienok na Samsung Galaxy Watch 7, 6, 5, 4",
        listed={"aukro": Decimal("6"), "vinted": Decimal("6")},
    )


def glass_44() -> InventoryItem:
    return InventoryItem(
        id="watch-glass-44",
        segment="commodity",
        title="2 ochranné sklá pre Samsung Galaxy Watch Active 2, 44mm",
        match_hints=["44"],
        listed={"ebay": Decimal("5"), "vinted": Decimal("5")},
    )


def glass_40() -> InventoryItem:
    return InventoryItem(
        id="watch-glass-40",
        segment="commodity",
        title="2 ochranné sklá pre Samsung Galaxy Watch Active 2, 40mm",
        match_hints=["40"],
        listed={"bazos": Decimal("5"), "ebay": Decimal("5")},
    )


def test_watch_strap_does_not_match_phone_want_ads() -> None:
    stock = [strap(), glass_44(), chip()]
    for title in (
        "Kúpim Samsung galaxy A 57. 8/265GB",
        "Kúpim poškodený Samsung Galaxy A56",
        "KÚPIM - Samsung Galaxy A17 5G 4GB / 128GB",
    ):
        assert is_want_to_buy(title)
        assert match_want(title, strap()) < 0.5
        assert best_item(title, stock) is None


def test_watch_glass_does_not_match_other_watch_want_ads() -> None:
    stock = [glass_40(), glass_44(), strap()]
    for title in (
        "KUPIM Samsung Galaxy Watch Ultra (2025) 47mm LTE SM-L705",
        "Kúpim Samsung galaxy watch ultra 2 (2026)",
        "Koupim samsung galaxy watch ultra 2",
        "Kupim Samsung galaxy watch ultra 2 2026 nove",
    ):
        assert is_want_to_buy(title)
        assert match_want(title, glass_40()) < 0.5
        assert match_want(title, glass_44()) < 0.5
        assert best_item(title, stock) is None


def test_watch_glass_matches_same_size_protector_want() -> None:
    title = "Kúpim ochranné sklo Samsung Galaxy Watch Active 2 44mm"
    assert is_want_to_buy(title)
    hit = best_item(title, [glass_40(), glass_44(), strap()])
    assert hit is not None
    assert hit[0].id == "watch-glass-44"
    assert hit[1] >= 0.5


def test_strap_queries_are_the_accessory_not_samsung() -> None:
    queries = queries_for(strap())
    blob = " ".join(queries).casefold()
    assert "remienok" in blob
    assert "samsung" not in blob


def test_photos_reject_a_phone_want_ad_even_if_titles_overlap() -> None:
    from io import BytesIO

    from PIL import Image

    from bazar_deals.selling.photos import photos_same_object

    def png(pattern: str) -> bytes:
        image = Image.new("RGB", (64, 64), (0, 0, 0))
        pixels = image.load()
        for y in range(64):
            for x in range(64):
                if pattern == "strap":
                    on = (y // 8) % 2 == 0
                else:
                    on = ((x // 8) + (y // 8)) % 2 == 0
                pixels[x, y] = (255, 255, 255) if on else (0, 0, 0)
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    strap_png = png("strap")
    phone_png = png("phone")
    blobs = {
        "https://img.example/strap.png": strap_png,
        "https://img.example/phone.png": phone_png,
    }
    item = strap().model_copy(update={"image_urls": ["https://img.example/strap.png"]})
    assert photos_same_object(
        item.image_urls,
        ["https://img.example/phone.png"],
        fetch=blobs.get,
    ) is False
    assert (
        best_item(
            "Kúpim remienok Samsung Galaxy Watch 7",
            [item],
            want_images=["https://img.example/phone.png"],
            fetch_image=blobs.get,
        )
        is None
    )
    hit = best_item(
        "Kúpim remienok Samsung Galaxy Watch 7",
        [item],
        want_images=["https://img.example/strap.png"],
        fetch_image=blobs.get,
    )
    assert hit is not None


def jadeite() -> InventoryItem:
    return InventoryItem(
        id="jadeit-brus",
        segment="minerals",
        title="Prírodný Jadeit - vybrúsený a vyleštený, na výrobu šperkov",
        species=["jadeit"],
        form="brus",
        color="zelený",
        listed={"aukro": Decimal("10"), "vinted": Decimal("12")},
    )


def pendant() -> InventoryItem:
    return InventoryItem(
        id="apatit-privesok",
        segment="minerals",
        title="Prívesok z prírodného lešteného modrého apatitu",
        species=["apatit"],
        form="prívesok",
        color="modrý",
        listed={"vinted": Decimal("6")},
    )


def test_pink_bracelet_want_does_not_match_green_jadeite_specimen() -> None:
    title = "Kupię bransoletke By dziubeka Btw2179 jadeit księży"
    ad = WantAd(
        marketplace="vinted",
        site="vinted.pl",
        external_id="2179",
        title=title,
        url="https://www.vinted.pl/items/2179",
        description="Kolor różowy, bransoletka jadeit By dziubeka.",
        raw={"brand": "By dziubeka", "dominant_colors": ["#e091b0"]},
    )
    assert is_want_to_buy(title)
    assert match_want(title, jadeite()) < 0.5
    assert match_want(ad, jadeite()) < 0.5
    assert best_item(ad, [jadeite(), crystal(), pendant()]) is None
    assert match_want("Kúpim ružový jadeit", jadeite()) < 0.5
    assert match_want("Kúpim zelený jadeit na výrobu šperkov", jadeite()) >= 0.5


def test_bracelet_want_does_not_match_apatite_pendant() -> None:
    title = "Kupię bransoletkę z niebieskiego apatytu"
    assert match_want(title, pendant()) < 0.5
    assert best_item(title, [pendant(), jadeite()]) is None


def test_mineral_species_want_still_matches_crystal_specimen() -> None:
    assert match_want("Kúpim ametyst", crystal()) >= 0.5
    hit = best_item("Kúpim ametyst z Namíbie Brandberg", [crystal(), jadeite()])
    assert hit is not None
    assert hit[0].id == "amethyst-namibia-74mm"


def test_vinted_photo_swatch_rejects_pink_on_green_stock() -> None:
    from io import BytesIO

    from PIL import Image

    from bazar_deals.selling.photos import hex_color_family, photos_color_conflict

    def png(color: tuple[int, int, int]) -> bytes:
        image = Image.new("RGB", (32, 32), color)
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    blobs = {
        "https://img.example/green.png": png((40, 160, 50)),
        "https://img.example/pink.png": png((220, 80, 140)),
    }
    item = jadeite().model_copy(update={"image_urls": ["https://img.example/green.png"]})
    assert hex_color_family("#e091b0") == "pink"
    assert hex_color_family("#2d8a3e") == "green"
    assert (
        photos_color_conflict(
            item.image_urls,
            ["https://img.example/pink.png"],
            fetch=blobs.get,
            stock_colors={"green"},
        )
        is True
    )
    ad = WantAd(
        marketplace="vinted",
        site="vinted.pl",
        external_id="1",
        title="Kupię jadeit",
        url="https://www.vinted.pl/items/1",
        image_urls=["https://img.example/pink.png"],
        raw={"dominant_colors": ["#e091b0"]},
    )
    assert best_item(ad, [item], fetch_image=blobs.get) is None


def test_ebay_429_retries_once_then_skips_remaining_storefronts() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        if "item_summary/search" in url:
            market = request.headers.get("X-EBAY-C-MARKETPLACE-ID", "")
            calls.append(market)
            return httpx.Response(429, text="Too Many Requests")
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="app-id", ebay_client_secret="cert", ebay_retention_enabled=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[chip()]), settings, client=client)

    assert calls, "Browse search should run after OAuth"
    assert calls[0] == "EBAY_DE"
    assert calls.count("EBAY_DE") == 2
    assert "EBAY_AT" not in calls
    assert "EBAY_PL" not in calls
    assert "EBAY_FR" not in calls
    throttle = [note for note in digest.notes if "429" in note]
    assert len(throttle) == 1
    assert "remaining storefronts skipped" in throttle[0]
    assert not any(note.startswith("ebay.at: HTTP 429") for note in digest.notes)
    assert not any(note.startswith("ebay.fr: HTTP 429") for note in digest.notes)


def test_facebook_and_olx_login_walls_are_skipped_not_retryable() -> None:
    from bazar_deals.research import retryable_sell_errors

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "facebook.com" in url:
            return httpx.Response(403, text="login required")
        if "olx.pl" in url:
            return httpx.Response(403, text="Forbidden")
        return _quiet_handler(request)

    settings = Settings(ebay_client_id="", ebay_client_secret="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = find_buyers(Inventory(items=[chip()]), settings, client=client)

    facebook = [note for note in digest.notes if note.startswith("facebook:")]
    olx = [note for note in digest.notes if note.startswith("olx.pl:")]
    assert facebook == ["facebook: skipped (public marketplace is a login wall)"]
    assert olx == ["olx.pl: skipped (public search is a login wall)"]
    assert retryable_sell_errors(digest.notes) == []
    body = format_buyer_digest(digest)
    assert "login wall" in body
    assert "facebook: HTTP 403" not in body
    assert "olx.pl: HTTP 403" not in body
    assert "public marketplace unavailable" not in body
    assert "source unavailable (not an empty result)" not in body

