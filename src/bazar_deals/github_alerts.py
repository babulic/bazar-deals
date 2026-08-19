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


def format_run_comment(deals: list[Deal], *, mention: str) -> str:
    markers = "\n".join(f"<!-- listing:{listing_key(deal)} -->" for deal in deals)
    ping = f"@{mention} " if mention else ""
    blocks = "\n\n".join(f"```\n{format_deal(deal)}\n```" for deal in deals)
    return (
        f"{markers}\n"
        f"{ping}**{len(deals)} deal(s)** this hunt\n\n"
        f"{blocks}\n"
    )


class GitHubIssueAlerts:
    """One standing issue; one comment per hunt run (GitHub emails @mentions)."""

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
        owner = self.repo.split("/")[0]
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{issue}/comments",
            json={"body": format_run_comment(fresh, mention=owner)},
        )
        return 1

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
                    f"@{owner} this issue is the deal inbox. Watch it and enable email for "
                    "**mentions** + **issue comments** in GitHub notification settings.\n\n"
                    "Each hunt run posts **one** comment with all new deals stacked."
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
