from __future__ import annotations

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Deal
from bazar_deals.notify import format_deal
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
    blocks = "\n\n".join(f"```\n{format_deal(deal)}\n```" for deal in deals)
    return (
        f"{ping}{markers}\n"
        f"**{len(deals)} deal(s)** this hunt\n\n"
        f"{blocks}\n"
    )


class GitHubIssueAlerts:
    """Collector issue like polymarket-tracker #4: assign + bot comment with @user."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.repo = (settings.github_repository or "").strip()
        self.token = settings.github_token
        self._issue_number = settings.github_alert_issue
        self._client = client

    def post_deals(self, deals: list[Deal]) -> int:
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
