import json

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Action, Condition, Deal, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.github_alerts import ALERT_LABEL, GitHubIssueAlerts, format_run_comment, listing_key
from decimal import Decimal


def _deal() -> Deal:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="1541",
        title="Commodore 1541-II",
        url="https://pc.bazos.sk/inzerat/1541/",
        price=Money(amount=Decimal("38"), currency="EUR"),
        condition=Condition.USED,
    )
    item = IdentifiedItem(
        listing=listing,
        vertical=Vertical.RETRO,
        canonical_name="Commodore 1541-II",
        confidence=0.9,
    )
    from bazar_deals.scoring import score_deal

    return score_deal(item, Decimal("89"), Decimal("8"))


def test_comment_embeds_hidden_listing_key() -> None:
    assert ALERT_LABEL == "bazar-alert"
    deal = _deal()
    body = format_run_comment([deal], mention="babulic")
    assert f"<!-- listing:{listing_key(deal)} -->" in body
    assert body.startswith("@babulic\n")
    assert "Commodore 1541-II" in body
    assert deal.action is Action.BUY


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
    settings = Settings(
        github_token="t",
        github_repository="babulic/bazar-deals",
    )
    with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
        alerts = GitHubIssueAlerts(settings, client=client)
        assert alerts.post_deals([first, second]) == 1
        assert alerts.post_deals([first, second]) == 0
    assert created["issues"] == 1
    assert patches[0]["labels"] == ["bazar-alert"]
    assert patches[0]["assignees"] == ["babulic"]
    assert len(posts) == 1
    assert posts[0].count("```") == 4
    assert posts[0].startswith("@babulic\n")
    assert "<!-- listing:bazos:1541 -->" in posts[0]
    assert "<!-- listing:bazos:1542 -->" in posts[0]
