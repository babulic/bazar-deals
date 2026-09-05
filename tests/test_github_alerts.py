import json
from decimal import Decimal

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import AIReview, Action, Condition, Deal, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.github_alerts import (
    ALERT_LABEL,
    GitHubIssueAlerts,
    format_hunt_comment,
    format_run_comment,
    listing_key,
    select_alert_deals,
)
from bazar_deals.scoring import score_deal


def _deal() -> Deal:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1541",
        title="Commodore 1541-II ORIGINAL LISTING TITLE",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal("38"), currency="EUR"),
        condition=Condition.USED,
        search_query="commodore 1541-ii",
    )
    item = IdentifiedItem(
        listing=listing,
        vertical=Vertical.RETRO,
        canonical_name="Commodore 1541-II",
        confidence=0.9,
        kind="hardware",
        search_query="commodore 1541-ii",
        asking_sample=12,
        sold_label="konzervatívna rýchlopredajná cena, ebay.de sold P25 (n=12)",
    )
    return score_deal(item, Decimal("120"), Decimal("8"))


def test_comment_embeds_listing_key_title_and_true_net_profit() -> None:
    assert ALERT_LABEL == "bazar-alert"
    deal = _deal()
    assert deal.action is Action.BUY
    assert deal.costs.net_profit >= Decimal("30")
    body = format_run_comment([deal], mention="babulic")
    assert f"<!-- listing:{listing_key(deal)} -->" in body
    assert body.startswith("@babulic\n")
    assert "Commodore 1541-II ORIGINAL LISTING TITLE" in body
    assert "- titulok inzerátu: [Commodore 1541-II ORIGINAL LISTING TITLE](https://pc.bazos.sk/inzerat/1541/)" in body
    assert "- identifikovaný tovar: Commodore 1541-II" in body
    assert "```" not in body
    assert "ALERT" not in body
    assert "- BUY: áno" in body
    assert "**BUY: áno**" in body
    assert "[inzerát](https://pc.bazos.sk/inzerat/1541/)" in body
    assert "- nákupná cena: 38 €" in body
    assert "- finálna konzervatívna rýchlopredajná cena: 120 €" in body
    assert "- rozdiel od obvyklej ceny: -82 € vs obvyklá (lacnejší)" in body
    assert "- nákupná doprava: 8 €" in body
    assert "- očakávaný čistý zisk:" in body
    assert "- price source: konzervatívna rýchlopredajná cena, ebay.de sold P25 (n=12)" in body


def test_comment_includes_ai_verification_and_sources() -> None:
    deal = _deal().model_copy(
        update={
            "ai_review": AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Commodore 1541-II disk drive",
                kind="hardware",
                quick_sale_price_eur=Decimal("110"),
                confidence=0.91,
                reason="Exact drive verified against sold listings.",
                source_urls=["https://www.ebay.de/sch/example"],
                model="gpt-5.6-terra",
            )
        }
    )
    body = format_run_comment([deal], mention="babulic")
    assert "- AI identifikácia: Commodore 1541-II disk drive" in body
    assert "- AI web quick-sale cena: 110 €" in body
    assert "- AI confidence: 0.91" in body
    assert "[zdroj 1](https://www.ebay.de/sch/example)" in body


def test_comment_includes_affiliate_markdown_link() -> None:
    deal = _deal()
    listing = deal.item.listing.model_copy(
        update={"affiliate_url": "https://www.ebay.de/itm/1541?campid=1"}
    )
    deal = deal.model_copy(update={"item": deal.item.model_copy(update={"listing": listing})})
    body = format_run_comment([deal], mention="babulic")
    assert "[affiliate](" in body
    assert "campid=1" in body


def test_hunt_status_comment_is_posted_even_without_buys() -> None:
    from collections import Counter

    from bazar_deals.domain import Marketplace
    from bazar_deals.pipeline import HuntRun

    run = HuntRun(
        deals=[],
        funnel=Counter(usable=10, scored=2, buy=0, no_sold_comps=8, sold_lookup_cap=0),
        source_stats={
            Marketplace.BAZOS: Counter(fetched=12, usable=10, scored=2, buy=0),
        },
        fetch_notes=[
            "bazos: fetched 12",
            "ebay: skipped (valuation uses Bazos/Aukro/Vinted price book, not eBay)",
        ],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert not body.startswith("@babulic")
    assert "**0 BUY áno**" in body
    assert "**BUY:" not in body
    assert "ebay: skipped" not in body
    assert "eBay" not in body
    assert "no_sold_comps=8" not in body
    assert "8 inzerátov bez 5 porovnateľných cien" in body
    assert "Funnel:" not in body
    assert "Priebeh:" in body
    assert "bazos: fetched 12" in body
    assert "žiadne ziskové karty" in body
    assert "zisk sa nerátal" not in body
    assert "Stratové a podprahové inzeráty sa neposielajú" in body
    assert "Ocenené inzeráty sú nižšie s odkazom" not in body


def test_unscored_hunt_does_not_claim_losing_cards() -> None:
    from collections import Counter

    from bazar_deals.domain import Marketplace
    from bazar_deals.pipeline import HuntRun

    run = HuntRun(
        deals=[],
        funnel=Counter(
            usable=238,
            scored=0,
            buy=0,
            no_sold_comps=83,
            sold_lookup_cap=154,
            below_net_profit=0,
            identity_weak=1,
            asking_only_comps=0,
            detail_failed=0,
        ),
        source_stats={
            Marketplace.BAZOS: Counter(fetched=501, usable=161, scored=0, buy=0),
            Marketplace.AUKRO: Counter(fetched=271, usable=77, scored=0, buy=0),
        },
        fetch_notes=[
            "bazos: fetched 501",
            "ebay: fetched 0 (eBay OAuth 401: client authentication failed)",
            "aukro: fetched 271",
            "vinted: fetched 0 (Vinted catalog blocked (DataDome/captcha). "
            "Hunt uses public HTML only — VINTED_ACCESS_KEY is sell-side Pro, not catalog search)",
        ],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert not body.startswith("@babulic")
    assert "**0 BUY áno**" in body
    assert "zisk sa nerátal" in body
    assert "žiadne ziskové karty" not in body
    assert "usable inzeráty nie sú ocenené" in body
    assert "Stratové položky sa neposielajú" not in body
    assert "identity_weak=1" not in body
    assert "1 bez spoľahlivej identity" in body
    assert "asking_only_comps=0" not in body
    assert "detail_failed=0" not in body
    assert "sold_lookup_cap=154" not in body
    assert "154 produktov" in body
    assert "below_net_profit=0" not in body
    assert "Funnel:" not in body
    assert "DataDome" in body
    assert "**BUY:" not in body
    assert "eBay OAuth" not in body
    assert "LOGIN_REQUIRED" not in body
    assert "ACCESS_NOT_GRANTED" not in body


def test_hunt_comment_omits_access_and_price_book_diagnostics() -> None:
    from collections import Counter

    from bazar_deals.domain import Marketplace
    from bazar_deals.pipeline import HuntRun

    run = HuntRun(
        deals=[],
        funnel=Counter(usable=10, scored=1, buy=0),
        source_stats={Marketplace.BAZOS: Counter(fetched=10, usable=10, scored=1, buy=0)},
        fetch_notes=[
            "bazos: fetched 10",
            "sbazar: NEEDS_DELIVERY_CONFIRMATION: 329 offers require detail or manual evidence",
            "facebook: fetched 0",
            "facebook: LOGIN_REQUIRED: manual import only; browser login is not unattended API access",
            "allegro_pl: fetched 0",
            "allegro_pl: ACCESS_NOT_GRANTED: authorized offers/listing access required; ALLEGRO_ACCESS_TOKEN alone does not grant permission; manual import available",
            "allegro_sk: fetched 0",
            "allegro_sk: ACCESS_NOT_GRANTED: authorized offers/listing access required; ALLEGRO_ACCESS_TOKEN alone does not grant permission; manual import available",
            "olx: fetched 0",
            "olx: BLOCKED: no readable public listing data",
            "olx: fetched 12",
            "ebay.de: fetched 9",
            "ebay.at: fetched 3",
            "price book: reused Bazos/Aukro/Vinted P25×0.75 from comps DB (product-role-v2:wlvs siltovka znacka nike stav nove, n=17)",
            "price book: live query budget exhausted (16); remaining products are unvalued",
        ],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert "bazos: fetched 10" in body
    assert "NEEDS_DELIVERY_CONFIRMATION" not in body
    assert "LOGIN_REQUIRED" not in body
    assert "ACCESS_NOT_GRANTED" not in body
    assert "manual import only" not in body
    assert "wlvs siltovka" not in body
    assert "live query budget exhausted" not in body
    assert "facebook: fetched 0" not in body
    assert "allegro_pl: fetched 0" not in body
    assert "olx: fetched 0" not in body
    assert "olx: fetched 12" in body
    assert "ebay.de: fetched 9" in body
    assert "ebay.at: fetched 3" in body


def test_hunt_progress_explains_cap_and_query_units() -> None:
    from collections import Counter

    from bazar_deals.domain import Marketplace
    from bazar_deals.pipeline import HuntRun

    run = HuntRun(
        deals=[],
        funnel=Counter(
            usable=2236,
            score_capped=2156,
            under_min=320,
            bulky=4,
            skip_keyword=0,
            heavy=5,
            identity_weak=0,
            detail_failed=24,
            scored=1,
            buy=0,
            no_sold_comps=59,
            sold_lookup_cap=39,
            asking_only_comps=0,
            below_net_profit=1,
            identity_ai_rescued=0,
            ai_rejected=0,
            ai_unavailable=0,
        ),
        source_stats={Marketplace.BAZOS: Counter(fetched=2000, usable=1800, scored=1, buy=0)},
        fetch_notes=["bazos: fetched 2000"],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert "Funnel:" not in body
    assert "score_capped=2156" not in body
    assert "sold_lookup_cap=39" not in body
    assert "no_sold_comps=59" not in body
    assert "skip_keyword=0" not in body
    assert "identity_weak=0" not in body
    assert "asking_only_comps=0" not in body
    assert "ai_rejected=0" not in body
    assert "2236 použiteľných inzerátov" in body
    assert "limit 80" in body
    assert "skúšalo 80" in body
    assert "2156 ostalo mimo" in body
    assert "1 ocenený pod prahom 30 €" in body
    assert "59 inzerátov bez 5 porovnateľných cien" in body
    assert "39 produktov" in body
    assert "to nie je počet inzerátov" in body
    assert "24 stránok inzerátu sa nenačítalo" in body
    assert "nesčíta sa to na limit" in body
    assert "320 pod 15 €" in body
    assert "4 rozmerné" in body
    assert "5 ťažké" in body
    assert "bazos: fetched 2000" in body
    assert "Marketplace:" not in body
    assert "stiahnuté" not in body


def test_hunt_progress_reports_persisted_batch_page_without_claiming_hourly_cap() -> None:
    from collections import Counter

    from bazar_deals.pipeline import BatchProgress, HuntRun

    run = HuntRun(
        deals=[],
        funnel=Counter(usable=80, no_sold_comps=5),
        source_stats={},
        fetch_notes=["loaded 80 cached listing(s)"],
        batch_progress=BatchProgress(
            batch_id="12345678abcdef",
            page=2,
            pages=29,
            start=80,
            end=160,
            total=2315,
        ),
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=20)
    assert "strana 2/29" in body
    assert "inzeráty 81–160 z 2315" in body
    assert "zostáva 2155" in body
    assert "nový fetch" in body
    assert "hodinovka" not in body
    assert "limit 80 za hunt" not in body


def test_alerts_are_buy_only_and_omit_losses() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun

    ranked = []
    for index in range(6):
        listing = _deal().item.listing.model_copy(
            update={
                "external_id": str(index),
                "url": f"https://pc.bazos.sk/inzerat/{index}/",
            }
        )
        item = _deal().item.model_copy(update={"listing": listing, "confidence": 0.5 + index / 20})
        ranked.append(score_deal(item, Decimal(str(70 + index)), Decimal("8")))
    buy = _deal()
    run = HuntRun(
        deals=[*ranked, buy],
        funnel=Counter(scored=7, buy=1),
        source_stats={},
        fetch_notes=["aukro: fetched 7"],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert body.startswith("@babulic\n")
    assert "**BUY: áno**" in body
    assert body.count("**BUY: áno**") == 1
    selected = select_alert_deals(run.deals)
    assert selected[0].action is Action.BUY
    assert len(selected) <= 5
    # Losses stay out: asking above usual with non-positive net profit.
    loss_listing = _deal().item.listing.model_copy(
        update={"external_id": "loss-over", "url": "https://pc.bazos.sk/inzerat/loss-over/"}
    )
    loss_item = _deal().item.model_copy(update={"listing": loss_listing})
    loss = score_deal(loss_item, Decimal("7"), Decimal("15"))
    assert loss.costs.net_profit <= 0
    mixed = HuntRun(
        deals=[buy, loss],
        funnel=Counter(scored=2, buy=1),
        source_stats={},
        fetch_notes=["aukro: fetched 2"],
    )
    mixed_body = format_hunt_comment(mixed, mention="babulic", min_profit=30)
    assert "https://pc.bazos.sk/inzerat/loss-over/" not in mixed_body
    assert select_alert_deals(mixed.deals) == [buy]


def test_profitable_under_threshold_ads_get_cards_without_a_ping() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun

    listing = _deal().item.listing.model_copy(
        update={"external_id": "near", "url": "https://pc.bazos.sk/inzerat/near/"}
    )
    item = _deal().item.model_copy(update={"listing": listing})
    skip = score_deal(item, Decimal("70"), Decimal("8"))
    assert skip.action is Action.SKIP
    assert skip.costs.net_profit > 0
    run = HuntRun(
        deals=[skip],
        funnel=Counter(scored=1, buy=0, below_net_profit=1),
        source_stats={},
        fetch_notes=["aukro: fetched 1"],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert not body.startswith("@babulic")
    assert "**0 BUY áno**" in body
    assert "najlepších stále ziskových inzerátov" in body
    assert "https://pc.bazos.sk/inzerat/near/" in body
    assert "**BUY: nie**" in body
    assert "- nákupná cena:" in body
    assert "- finálna konzervatívna rýchlopredajná cena:" in body
    assert "- rozdiel od obvyklej ceny:" in body
    assert select_alert_deals(run.deals) == [skip]


def test_ai_rejected_typical_is_not_a_still_profitable_card() -> None:
    from collections import Counter

    from bazar_deals.notify import is_cheaper_than_usual
    from bazar_deals.pipeline import HuntRun

    listing = _deal().item.listing.model_copy(
        update={
            "external_id": "287558443831",
            "title": "Computing Videothek Billardspiele Commodore 64/128",
            "url": "https://www.ebay.de/itm/287558443831",
            "price": Money(amount=Decimal("24.40"), currency="EUR"),
        }
    )
    item = _deal().item.model_copy(
        update={
            "listing": listing,
            "canonical_name": "Computing Videothek Billardspiele Commodore 64/128",
            "kind": "generic",
        }
    )
    skip = score_deal(item, Decimal("104.25"), Decimal("8"))
    skip = skip.model_copy(
        update={
            "action": Action.SKIP,
            "reason": "AI rejected candidate: generic software label, not one verifiable SKU",
            "ai_review": AIReview(
                approved=False,
                complete_product=False,
                canonical_name="C64 cassette game, not a computer",
                kind="media",
                quick_sale_price_eur=None,
                confidence=0.2,
                reason="One game on cassette is not a Commodore 64 computer.",
            ),
        }
    )
    assert skip.costs.net_profit > 0
    assert not is_cheaper_than_usual(skip)
    run = HuntRun(
        deals=[skip],
        funnel=Counter(scored=1, buy=0, ai_rejected=1, below_net_profit=1),
        source_stats={},
        fetch_notes=["ebay.de: fetched 1"],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert select_alert_deals(run.deals) == []
    assert "https://www.ebay.de/itm/287558443831" not in body
    assert "104.25" not in body
    assert "**BUY: nie**" not in body
    assert "AI zamietlo" in body


def test_losing_hunts_post_status_without_cards() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun

    listing = _deal().item.listing.model_copy(
        update={"external_id": "loss", "url": "https://pc.bazos.sk/inzerat/loss/"}
    )
    item = _deal().item.model_copy(update={"listing": listing})
    skip = score_deal(item, Decimal("7"), Decimal("15"))
    assert skip.action is Action.SKIP
    assert skip.costs.net_profit <= 0
    run = HuntRun(
        deals=[skip],
        funnel=Counter(scored=1, buy=0),
        source_stats={},
        fetch_notes=["aukro: fetched 1"],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert not body.startswith("@babulic")
    assert "**0 BUY áno**" in body
    assert "Stratové a podprahové inzeráty sa neposielajú" in body
    assert "https://pc.bazos.sk/inzerat/loss/" not in body
    assert "**BUY:" not in body
    assert select_alert_deals(run.deals) == []


def test_overpriced_scored_ads_and_misses_are_not_listed() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun
    from bazar_deals.soldcomps import PriceBookMiss

    listing = _deal().item.listing.model_copy(
        update={
            "external_id": "siltovka",
            "title": "wlvs siltovka",
            "url": "https://www.vinted.sk/items/9849277566-wlvs-siltovka",
            "price": Money(amount=Decimal("20"), currency="EUR"),
        }
    )
    item = _deal().item.model_copy(update={"listing": listing})
    skip = score_deal(item, Decimal("7.28"), Decimal("15"))
    assert skip.action is Action.SKIP
    assert skip.costs.buy_price > skip.costs.estimated_resale
    expensive_miss = PriceBookMiss(
        listing=listing.model_copy(
            update={
                "external_id": "kabat",
                "title": "Pravá koža kabát",
                "url": "https://www.vinted.sk/items/9849540264-prava-koza-kabat",
            }
        ),
        query="prava koza kabat",
        peer_count=1,
        required=5,
        typical=Decimal("11.25"),
    )
    run = HuntRun(
        deals=[],
        funnel=Counter(scored=0, buy=0, above_typical=1),
        source_stats={Marketplace.VINTED: Counter(fetched=1909, usable=1903, scored=0, buy=0)},
        fetch_notes=["vinted: fetched 1909"],
        price_book_misses=[expensive_miss],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert "žiadne ocenené kandidáty" in body
    assert "nákup nad obvyklou" in body
    assert "nie ocenené, nie deal" in body
    assert "### Lacnejšie ako obvyklá" not in body
    assert "### Ocenené inzeráty" not in body
    assert "wlvs siltovka" not in body
    assert "čistý zisk -" not in body
    assert "Pravá koža kabát" not in body
    assert "### Málo porovnateľných" not in body
    assert "Marketplace:" not in body
    assert "ocenený drahší" not in body


def test_buy_alerts_are_capped_at_top_n() -> None:
    deals = []
    for index in range(6):
        listing = _deal().item.listing.model_copy(update={"external_id": f"buy-{index}"})
        item = _deal().item.model_copy(update={"listing": listing})
        deals.append(score_deal(item, Decimal("120"), Decimal("8")))
    selected = select_alert_deals(deals)
    assert len(selected) == 5
    assert all(deal.action is Action.BUY for deal in selected)


def test_alert_writer_ignores_non_buy_deals() -> None:
    deal = _deal().model_copy(update={"action": Action.SKIP})
    settings = Settings(github_token="t", github_repository="babulic/bazar-deals")
    alerts = GitHubIssueAlerts(settings)
    assert alerts.post_deals([deal]) == 0


def test_one_comment_for_several_deals_then_skip_duplicates() -> None:
    first = _deal()
    second = _deal().model_copy(
        update={
            "item": first.item.model_copy(
                update={
                    "listing": first.item.listing.model_copy(update={"external_id": "1542"})
                }
            )
        }
    )
    posts: list[str] = []
    created = {"issues": 0}
    patches: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/labels/" in path and request.method == "GET":
            return httpx.Response(200, json={"name": "bazar-alert"})
        if path.endswith("/labels") and request.method == "POST":
            return httpx.Response(201, json={"name": "bazar-alert"})
        if request.method == "PATCH" and "/issues/" in path:
            patches.append(json.loads(request.content))
            return httpx.Response(200, json={"number": 7})
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/issues"):
            created["issues"] += 1
            return httpx.Response(201, json={"number": 7})
        if request.method == "GET" and path.endswith("/comments"):
            bodies = [{"body": comment} for comment in posts]
            return httpx.Response(200, json=bodies)
        if request.method == "POST" and path.endswith("/comments"):
            payload = json.loads(request.content)
            posts.append(payload["body"])
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={"message": path})

    transport = httpx.MockTransport(handler)
    settings = Settings(github_token="t", github_repository="babulic/bazar-deals")
    with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
        alerts = GitHubIssueAlerts(settings, client=client)
        assert alerts.post_deals([first, second]) == 1
        assert alerts.post_deals([first, second]) == 0
    assert created["issues"] == 1
    assert patches[0]["labels"] == ["bazar-alert"]
    assert patches[0]["assignees"] == ["babulic"]
    assert len(posts) == 1
    assert posts[0].count("[inzerát](") == 2
    assert "<!-- listing:bazos:1541 -->" in posts[0]
    assert "<!-- listing:bazos:1542 -->" in posts[0]


def test_post_run_posts_zero_buy_status_without_mention() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun

    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/labels/" in path and request.method == "GET":
            return httpx.Response(200, json={"name": "bazar-alert"})
        if request.method == "PATCH" and "/issues/" in path:
            return httpx.Response(200, json={"number": 1})
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[{"number": 1, "title": "Deal alerts"}])
        if request.method == "GET" and path.endswith("/comments"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/comments"):
            posts.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"id": 9})
        return httpx.Response(404, json={"message": path})

    settings = Settings(
        github_token="t",
        github_repository="babulic/bazar-deals",
        github_alert_issue=1,
        github_assignee="babulic",
    )
    run = HuntRun(deals=[], funnel=Counter(buy=0, usable=3), source_stats={}, fetch_notes=["vinted: fetched 0"])
    with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)) as client:
        assert GitHubIssueAlerts(settings, client=client).post_run(run) == 1
    assert len(posts) == 1
    assert not posts[0].startswith("@babulic")
    assert "**0 BUY áno**" in posts[0]
    assert "vinted: fetched 0" in posts[0]


def test_post_run_posts_profitable_near_misses_without_mention() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun

    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/labels/" in path and request.method == "GET":
            return httpx.Response(200, json={"name": "bazar-alert"})
        if request.method == "PATCH" and "/issues/" in path:
            return httpx.Response(200, json={"number": 1})
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[{"number": 1, "title": "Deal alerts"}])
        if request.method == "GET" and path.endswith("/comments"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/comments"):
            posts.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"id": 9})
        return httpx.Response(404, json={"message": path})

    listing = _deal().item.listing.model_copy(
        update={"external_id": "near", "url": "https://pc.bazos.sk/inzerat/near/"}
    )
    item = _deal().item.model_copy(update={"listing": listing})
    skip = score_deal(item, Decimal("70"), Decimal("8"))
    settings = Settings(
        github_token="t",
        github_repository="babulic/bazar-deals",
        github_alert_issue=1,
        github_assignee="babulic",
    )
    run = HuntRun(
        deals=[skip],
        funnel=Counter(buy=0, scored=1, below_net_profit=1),
        source_stats={},
        fetch_notes=["aukro: fetched 1"],
    )
    with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)) as client:
        assert GitHubIssueAlerts(settings, client=client).post_run(run) == 1
    assert len(posts) == 1
    assert not posts[0].startswith("@babulic")
    assert "https://pc.bazos.sk/inzerat/near/" in posts[0]
    assert "**BUY: nie**" in posts[0]


def test_price_book_misses_use_listing_links_prices_and_delta() -> None:
    from collections import Counter

    from bazar_deals.pipeline import HuntRun
    from bazar_deals.soldcomps import PriceBookMiss

    listing = _deal().item.listing
    peer = listing.model_copy(
        update={
            "external_id": "peer",
            "url": "https://pc.bazos.sk/inzerat/peer/",
            "title": "Commodore 1541-II peer",
            "price": Money(amount=Decimal("90"), currency="EUR"),
        }
    )
    miss = PriceBookMiss(
        listing=listing,
        query="commodore 1541-ii",
        peer_count=1,
        required=5,
        typical=Decimal("67.50"),
        peers=(peer,),
    )
    run = HuntRun(
        deals=[],
        funnel=Counter(usable=1, scored=0, buy=0, no_sold_comps=1),
        source_stats={},
        fetch_notes=[
            "bazos: fetched 1",
            "price book: insufficient comparable ads (commodore 1541-ii, n=1, required=5)",
        ],
        price_book_misses=[miss],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert "insufficient comparable ads (commodore 1541-ii" not in body
    assert "### Málo porovnateľných inzerátov" not in body
    assert "https://pc.bazos.sk/inzerat/1541/" not in body
    assert "https://pc.bazos.sk/inzerat/peer/" not in body
    assert "s odkazom, nákupnou cenou a rozdielom od obvyklej" not in body
