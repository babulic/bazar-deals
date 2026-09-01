from __future__ import annotations

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Action, Deal
from bazar_deals.notify import format_compact_deal, format_github_deal, format_price_book_miss
from bazar_deals.pipeline import HuntRun, is_alert_noise
from bazar_deals.rules import rules

ALERT_ISSUE_TITLE = rules()["github"]["alert_issue_title"]
ALERT_LABEL = rules()["github"]["alert_label"]
ALERT_TOP_N = int(rules()["github"].get("alert_top_n", 5))
SELL_ALERT_ISSUE_TITLE = str(rules()["github"].get("sell_alert_issue_title") or "Sell buyers")
SELL_ALERT_LABEL = str(rules()["github"].get("sell_alert_label") or "bazar-sell")
_API = "https://api.github.com"


def listing_key(deal: Deal) -> str:
    listing = deal.item.listing
    return f"{listing.marketplace.value}:{listing.external_id}"


def select_alert_deals(deals: list[Deal], *, limit: int | None = None) -> list[Deal]:
    """BUY deals only, ranked by expected net profit, capped at `alert_top_n`.

    Full cards stay BUY-only. Scored losses and price-book misses are listed
    separately with listing links, asking price, and delta vs usual.
    """
    cap = ALERT_TOP_N if limit is None else max(0, int(limit))
    buys = [deal for deal in deals if deal.action is Action.BUY]
    ranked = sorted(
        buys,
        key=lambda deal: (deal.costs.net_profit, deal.item.confidence),
        reverse=True,
    )
    return ranked[:cap]


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
    """Hunt report: BUY cards, then scored ads with links, then thin price-book misses."""
    shown = select_alert_deals(run.deals)
    buy_count = sum(1 for deal in run.deals if deal.action is Action.BUY)
    ping = f"@{mention}\n\n" if mention and buy_count else ""
    markers = "\n".join(f"<!-- listing:{listing_key(deal)} -->" for deal in shown)
    status = _format_status(run, min_profit=min_profit, buy_count=buy_count, shown=len(shown))
    sections = [f"{ping}{markers}\n{status}" if markers else f"{ping}{status}"]
    if shown:
        sections.append("\n\n---\n\n".join(format_github_deal(deal) for deal in shown))
    watch = _scored_watch(run.deals, shown)
    if watch:
        rows = "\n".join(f"- {format_compact_deal(deal)}" for deal in watch)
        sections.append(f"### Ocenené inzeráty (pod prahom {min_profit} €)\n\n{rows}")
    misses = list(run.price_book_misses)
    if misses:
        rows = "\n".join(f"- {format_price_book_miss(miss)}" for miss in misses)
        sections.append(
            "### Málo porovnateľných inzerátov\n\n"
            "Titulok je odkaz na inzerát. U tenkého vzorku je obvyklá cena P25×0.75 "
            "z nájdených peerov, nie buy signál.\n\n"
            f"{rows}"
        )
    return "\n\n".join(section.rstrip() for section in sections) + "\n"


def _scored_watch(deals: list[Deal], shown: list[Deal]) -> list[Deal]:
    shown_keys = {listing_key(deal) for deal in shown}
    return [deal for deal in deals if listing_key(deal) not in shown_keys]


def _status_notes(run: HuntRun) -> str:
    notes = [note for note in run.fetch_notes if not is_alert_noise(note)]
    return "\n".join(f"- {note}" for note in notes) or "- (no sources fetched)"


def _format_status(run: HuntRun, *, min_profit, buy_count: int, shown: int) -> str:
    notes = _status_notes(run)
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
        f"score_capped={run.funnel.get('score_capped', 0)}",
        f"under_min={run.funnel.get('under_min', 0)}",
        f"bulky={run.funnel.get('bulky', 0)}",
        f"skip_keyword={run.funnel.get('skip_keyword', 0)}",
        f"heavy={run.funnel.get('heavy', 0)}",
        f"identity_weak={run.funnel.get('identity_weak', 0)}",
        f"detail_failed={run.funnel.get('detail_failed', 0)}",
        f"scored={run.funnel.get('scored', 0)}",
        f"buy={run.funnel.get('buy', 0)}",
        f"no_sold_comps={run.funnel.get('no_sold_comps', 0)}",
        f"sold_lookup_cap={run.funnel.get('sold_lookup_cap', 0)}",
        f"asking_only_comps={run.funnel.get('asking_only_comps', 0)}",
        f"below_net_profit={run.funnel.get('below_net_profit', 0)}",
        f"identity_ai_rescued={run.funnel.get('identity_ai_rescued', 0)}",
        f"ai_rejected={run.funnel.get('ai_rejected', 0)}",
        f"ai_unavailable={run.funnel.get('ai_unavailable', 0)}",
    ]
    scored = int(run.funnel.get("scored", 0) or 0)
    miss_n = len(run.price_book_misses)
    if buy_count:
        headline = (
            f"**{buy_count} BUY áno** · {shown} ziskových kariet podľa očakávaného čistého zisku "
            f"(prah {min_profit} €)."
        )
    elif scored == 0:
        headline = (
            f"**0 BUY áno** · zisk sa nerátal — usable inzeráty nie sú ocenené "
            f"(chýba trhový cenník Bazos/Aukro/Vinted / málo podobných inzerátov). "
            f"Toto nie je dôkaz, že sú stratové."
        )
        if miss_n:
            headline += f" {miss_n} inzerátov bez 5 peerov je nižšie s odkazom, nákupnou cenou a rozdielom od obvyklej."
    else:
        headline = (
            f"**0 BUY áno** · žiadne ziskové karty (prah {min_profit} € čistého zisku). "
            f"Ocenené inzeráty sú nižšie s odkazom, nákupnou cenou a rozdielom od obvyklej ceny."
        )
    return (
        f"{headline}\n\n"
        f"Zdroje:\n{notes}\n\n"
        f"Funnel: {' '.join(funnel_bits)}\n\n"
        f"Marketplace:\n" + "\n".join(health)
    )


class GitHubIssueAlerts:
    """Collector issue for BUY hunt cards, or sell-side buyer digests."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        issue_title: str | None = None,
        issue_label: str | None = None,
        issue_number: int | None = None,
        label_color: str = "D93F0B",
        label_description: str = "Automatické hunt alerty",
    ) -> None:
        self.settings = settings
        self.repo = (settings.github_repository or "").strip()
        self.token = settings.github_token
        self._issue_title = issue_title or ALERT_ISSUE_TITLE
        self._issue_label = issue_label or ALERT_LABEL
        self._issue_number = settings.github_alert_issue if issue_number is None else issue_number
        self._label_color = label_color
        self._label_description = label_description
        self._client = client

    @classmethod
    def for_sell_buyers(cls, settings: Settings, client: httpx.Client | None = None) -> GitHubIssueAlerts:
        return cls(
            settings,
            client,
            issue_title=SELL_ALERT_ISSUE_TITLE,
            issue_label=SELL_ALERT_LABEL,
            issue_number=settings.github_sell_alert_issue,
            label_color="0E8A16",
            label_description="Kupci na vlastný tovar",
        )

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

    def post_buyer_digest(self, body: str) -> int:
        """Post the sell-side buyer digest even when no want-ad matched."""
        self._require_auth()
        issue = self.ensure_issue()
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
                params={"state": "open", "labels": self._issue_label, "per_page": 100},
            ):
                if issue.get("pull_request"):
                    continue
                if issue.get("title") == self._issue_title:
                    number = int(issue["number"])
                    break
        if not number:
            created = self._request(
                "POST",
                f"/repos/{self.repo}/issues",
                json={
                    "title": self._issue_title,
                    "labels": [self._issue_label],
                    "assignees": [self._assignee()],
                    "body": (
                        "Collector issue for hunt or sell-buyer alerts. "
                        "`github-actions[bot]` comments here and mentions the assignee."
                    ),
                },
            )
            number = int(created["number"])
        self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            json={"assignees": [self._assignee()], "state": "open", "labels": [self._issue_label]},
        )
        self._issue_number = number
        return number

    def _assignee(self) -> str:
        return (self.settings.github_assignee or self.repo.split("/")[0]).lstrip("@")

    def _ensure_label(self) -> None:
        path = f"/repos/{self.repo}/labels/{self._issue_label}"
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
                    "name": self._issue_label,
                    "color": self._label_color,
                    "description": self._label_description,
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
