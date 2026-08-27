import json
import re
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
    assert "- titulok inzerátu: Commodore 1541-II ORIGINAL LISTING TITLE" in body
    assert "- identifikovaný tovar: Commodore 1541-II" in body
    assert "```" not in body
    assert "ALERT" not in body
    assert not re.search(r"\bBUY\b", body)
    assert "[inzerát](https://pc.bazos.sk/inzerat/1541/)" in body
    assert "- nákupná cena: 38 €" in body
    assert "- finálna konzervatívna rýchlopredajná cena: 120 €" in body
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
            "ebay: fetched 0 (set GitHub Actions secrets EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)",
        ],
    )
    body = format_hunt_comment(run, mention="babulic", min_profit=30)
    assert not body.startswith("@babulic")
    assert "**0 BUY**" in body
    assert "EBAY_CLIENT_ID" in body
    assert "no_sold_comps=8" in body
    assert "bazos: fetched 12" in body


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


def test_post_run_writes_a_status_comment_when_there_are_no_buys() -> None:
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
    )
    run = HuntRun(deals=[], funnel=Counter(buy=0, usable=3), source_stats={}, fetch_notes=["vinted: fetched 0"])
    with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)) as client:
        assert GitHubIssueAlerts(settings, client=client).post_run(run) == 1
    assert len(posts) == 1
    assert "**0 BUY**" in posts[0]
    assert "vinted: fetched 0" in posts[0]
