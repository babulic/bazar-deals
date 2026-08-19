from __future__ import annotations

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Deal
from bazar_deals.notify import format_deal

ALERT_ISSUE_TITLE = "Deal alerts"
_API = "https://api.github.com"


def listing_key(deal: Deal) -> str:
    listing = deal.item.listing
    return f"{listing.marketplace.value}:{listing.external_id}"


def format_issue_comment(deal: Deal) -> str:
    key = listing_key(deal)
    return (
        f"<!-- listing:{key} -->\n"
        f"**{deal.action.value.upper()}** · {deal.item.vertical.value if deal.item.vertical else 'unclassified'}\n\n"
        f"```\n{format_deal(deal)}\n```\n"
    )


class GitHubIssueAlerts:
    """One standing issue; each deal is a comment (GitHub emails subscribers)."""

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
        posted = 0
        for deal in deals:
            key = listing_key(deal)
            if key in seen:
                continue
            self._request(
                "POST",
                f"/repos/{self.repo}/issues/{issue}/comments",
                json={"body": format_issue_comment(deal)},
            )
            seen.add(key)
            posted += 1
        return posted

    def ensure_issue(self) -> int:
        self._require_auth()
        if self._issue_number:
            return self._issue_number
        for issue in self._request("GET", f"/repos/{self.repo}/issues", params={"state": "open", "per_page": 100}):
            if issue.get("pull_request"):
                continue
            if issue.get("title") == ALERT_ISSUE_TITLE:
                self._issue_number = int(issue["number"])
                return self._issue_number
        owner = self.repo.split("/")[0]
        created = self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={
                "title": ALERT_ISSUE_TITLE,
                "body": (
                    f"@{owner} subscribe to this issue (Watch → Custom → Issues, or the Subscribe button).\n\n"
                    "Each mispricing alert is a **new comment**. GitHub emails you; the last comment is the latest deal."
                ),
            },
        )
        self._issue_number = int(created["number"])
        return self._issue_number

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
