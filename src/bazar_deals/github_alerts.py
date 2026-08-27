from __future__ import annotations

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Action, Deal
from bazar_deals.notify import format_github_deal
from bazar_deals.pipeline import HuntRun
from bazar_deals.rules import rules

ALERT_ISSUE_TITLE = rules()["github"]["alert_issue_title"]
ALERT_LABEL = rules()["github"]["alert_label"]
_API = "https://api.github.com"


def listing_key(deal: Deal) -> str:
    listing = deal.item.listing
    return f"{listing.marketplace.value}:{listing.external_id}"


def format_run_comment(deals: list[Deal], *, mention: str) -> str:
    markers = "\n".join(f"<!-- listing:{listing_key(deal)} -->" for deal in deals)
    ping = f"@{mention}\n\n" if mention else ""
    blocks = "\n\n---\n\n".join(format_github_deal(deal) for deal in deals)
    return (
        f"{ping}{markers}\n"
        f"**{len(deals)} deal(s)** this hunt\n\n"
        f"{blocks}\n"
    )


def format_hunt_comment(
    run: HuntRun,
    *,
    mention: str,
    min_profit,
) -> str:
    """Always-visible hunt report. Mentions the assignee only when there is a BUY."""
    buys = [deal for deal in run.deals if deal.action is Action.BUY]
    ping = f"@{mention}\n\n" if mention and buys else ""
    markers = "\n".join(f"<!-- listing:{listing_key(deal)} -->" for deal in buys)
    status = _format_status(run, min_profit=min_profit, buy_count=len(buys))
    if not buys:
        return f"{ping}{status}\n"
    blocks = "\n\n---\n\n".join(format_github_deal(deal) for deal in buys)
    marker_block = f"{markers}\n" if markers else ""
    return f"{ping}{marker_block}{status}\n\n{blocks}\n"


def _format_status(run: HuntRun, *, min_profit, buy_count: int) -> str:
    notes = "\n".join(f"- {note}" for note in run.fetch_notes) or "- (no sources fetched)"
    health = []
    for market, stats in run.source_stats.items():
        health.append(
            f"- {market.value}: fetched {stats.get('fetched', 0)}, "
            f"usable {stats.get('usable', 0)}, scored {stats.get('scored', 0)}, "
            f"buy {stats.get('buy', 0)}"
        )
    if not health:
        health.append("- no marketplace reached scoring")
    funnel_bits = [
        f"usable={run.funnel.get('usable', 0)}",
        f"scored={run.funnel.get('scored', 0)}",
        f"buy={run.funnel.get('buy', 0)}",
        f"no_sold_comps={run.funnel.get('no_sold_comps', 0)}",
        f"sold_lookup_cap={run.funnel.get('sold_lookup_cap', 0)}",
        f"below_net_profit={run.funnel.get('below_net_profit', 0)}",
        f"identity_ai_rescued={run.funnel.get('identity_ai_rescued', 0)}",
        f"ai_rejected={run.funnel.get('ai_rejected', 0)}",
        f"ai_unavailable={run.funnel.get('ai_unavailable', 0)}",
    ]
    if buy_count:
        headline = f"**{buy_count} BUY deal(s)** this hunt (prah {min_profit} € čistého zisku)"
    else:
        headline = (
            f"**0 BUY** this hunt — žiadny inzerát neprešiel prah {min_profit} € čistého zisku. "
            "Deal alerty sa posielajú len na BUY."
        )
    return (
        f"{headline}\n\n"
        f"Zdroje:\n{notes}\n\n"
        f"Funnel: {' '.join(funnel_bits)}\n\n"
        f"Marketplace:\n" + "\n".join(health)
    )


class GitHubIssueAlerts:
    """Collector issue for actionable BUY deals only."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.repo = (settings.github_repository or "").strip()
        self.token = settings.github_token
        self._issue_number = settings.github_alert_issue
        self._client = client

    def post_deals(self, deals: list[Deal]) -> int:
        deals = [deal for deal in deals if deal.action is Action.BUY]
        if not deals:
            return 0
        issue = self.ensure_issue()
        seen = self._seen_keys(issue)
        fresh = [deal for deal in deals if listing_key(deal) not in seen]
        if not fresh:
            return 0
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{issue}/comments",
            json={"body": format_run_comment(fresh, mention=self._assignee())},
        )
        return 1

    def post_run(self, run: HuntRun) -> int:
        """Post the hunt report even when there is no BUY, so the collector is never blank."""
        self._require_auth()
        issue = self.ensure_issue()
        body = format_hunt_comment(
            run,
            mention=self._assignee(),
            min_profit=self.settings.min_net_profit_eur,
        )
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{issue}/comments",
            json={"body": body},
        )
        return 1

    def ensure_issue(self) -> int:
        self._require_auth()
        self._ensure_label()
        number = self._issue_number
        if not number:
            for issue in self._request(
                "GET",
                f"/repos/{self.repo}/issues",
                params={"state": "open", "labels": ALERT_LABEL, "per_page": 100},
            ):
                if issue.get("pull_request"):
                    continue
                if issue.get("title") == ALERT_ISSUE_TITLE:
                    number = int(issue["number"])
                    break
        if not number:
            created = self._request(
                "POST",
                f"/repos/{self.repo}/issues",
                json={
                    "title": ALERT_ISSUE_TITLE,
                    "labels": [ALERT_LABEL],
                    "assignees": [self._assignee()],
                    "body": (
                        "Collector issue for hunt alerts. "
                        "`github-actions[bot]` comments here and mentions the assignee."
                    ),
                },
            )
            number = int(created["number"])
        self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            json={"assignees": [self._assignee()], "state": "open", "labels": [ALERT_LABEL]},
        )
        self._issue_number = number
        return number

    def _assignee(self) -> str:
        return (self.settings.github_assignee or self.repo.split("/")[0]).lstrip("@")

    def _ensure_label(self) -> None:
        path = f"/repos/{self.repo}/labels/{ALERT_LABEL}"
        try:
            self._request("GET", path)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        try:
            self._request(
                "POST",
                f"/repos/{self.repo}/labels",
                json={
                    "name": ALERT_LABEL,
                    "color": "D93F0B",
                    "description": "Automatické hunt alerty",
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise

    def _seen_keys(self, issue: int) -> set[str]:
        keys: set[str] = set()
        page = 1
        while page <= 10:
            comments = self._request(
                "GET",
                f"/repos/{self.repo}/issues/{issue}/comments",
                params={"per_page": 100, "page": page},
            )
            if not comments:
                break
            for comment in comments:
                body = comment.get("body") or ""
                for line in body.splitlines():
                    if line.startswith("<!-- listing:") and line.endswith("-->"):
                        keys.add(line[len("<!-- listing:") : -3].strip())
            if len(comments) < 100:
                break
            page += 1
        return keys

    def _require_auth(self) -> None:
        if not self.token:
            raise RuntimeError("Set GITHUB_TOKEN to post deal comments")
        if not self.repo or "/" not in self.repo:
            raise RuntimeError("Set GITHUB_REPOSITORY to owner/name")

    def _request(self, method: str, path: str, **kwargs):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._client is not None:
            response = self._client.request(method, path, headers=headers, **kwargs)
        else:
            response = httpx.request(method, _API + path, headers=headers, timeout=20.0, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()
