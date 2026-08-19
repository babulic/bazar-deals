import json

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Action, Condition, Deal, IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.github_alerts import GitHubIssueAlerts, format_issue_comment, listing_key
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
    deal = _deal()
    body = format_issue_comment(deal)
    assert f"<!-- listing:{listing_key(deal)} -->" in body
    assert "Commodore 1541-II" in body
    assert deal.action is Action.BUY


def test_posts_comment_and_skips_duplicate() -> None:
    deal = _deal()
    posts: list[str] = []
    created = {"issues": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
        assert alerts.post_deals([deal]) == 1
        assert alerts.post_deals([deal]) == 0
    assert created["issues"] == 1
    assert len(posts) == 1
