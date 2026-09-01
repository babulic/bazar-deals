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


def _funnel_n(run: HuntRun, key: str) -> int:
    return int(run.funnel.get(key, 0) or 0)


def _format_progress(run: HuntRun, *, min_profit) -> str:
    """Slovak drop-off, only non-zero counts, with units so the numbers add up."""
    n = lambda key: _funnel_n(run, key)
    hunt = rules()["hunt"]
    score_cap = int(hunt.get("max_score_listings", 80))
    min_buy = hunt.get("min_buy_eur", 20)
    max_buy = hunt.get("max_buy_eur", 110)
    usable = n("usable")
    capped = n("score_capped")
    tried = max(0, usable - capped) if usable else 0
    lines: list[str] = []

    if usable:
        if capped:
            lines.append(
                f"- {usable} použiteľných inzerátov (kúpiť hneď, {min_buy}–{max_buy} €). "
                f"Ocenenie má limit {score_cap} za hunt, takže sa skúšalo {tried} "
                f"a {capped} ostalo mimo — hodinovka nestihne otvárať tisíce stránok."
            )
        else:
            lines.append(
                f"- {usable} použiteľných inzerátov (kúpiť hneď, {min_buy}–{max_buy} €)."
            )

    scored_bits: list[str] = []
    if n("buy"):
        scored_bits.append(f"{n('buy')} BUY áno")
    if n("below_net_profit"):
        count = n("below_net_profit")
        word = "ocenený" if count == 1 else "ocenených"
        scored_bits.append(f"{count} {word} pod prahom {min_profit} €")
    elif n("scored") and not n("buy"):
        scored_bits.append(f"{n('scored')} ocenených")
    if n("no_sold_comps"):
        scored_bits.append(
            f"{n('no_sold_comps')} inzerátov bez 5 porovnateľných cien (nie sú stratové)"
        )
    if n("identity_weak"):
        scored_bits.append(f"{n('identity_weak')} bez spoľahlivej identity")
    if n("insufficient_detail"):
        scored_bits.append(f"{n('insufficient_detail')} s príliš krátkym textom")
    if n("asking_only_comps"):
        scored_bits.append(f"{n('asking_only_comps')} len s predbežným cenníkom")
    if n("identity_ai_rescued"):
        scored_bits.append(f"{n('identity_ai_rescued')} s identitou doplnenou AI")
    if n("ai_rejected"):
        scored_bits.append(f"{n('ai_rejected')} AI zamietlo")
    if n("ai_unavailable"):
        scored_bits.append(f"{n('ai_unavailable')} bez AI review")
    if scored_bits:
        prefix = f"z tých {tried}: " if capped and tried else ""
        lines.append(f"- {prefix}{', '.join(scored_bits)}.")

    if n("sold_lookup_cap"):
        lines.append(
            f"- cenník vynechal {n('sold_lookup_cap')} produktov (limit live query, "
            "to nie je počet inzerátov). Bez ceny to nie je strata."
        )
    if n("detail_failed"):
        lines.append(
            f"- {n('detail_failed')} stránok inzerátu sa nenačítalo. "
            "Tento počet sa prekrýva s riadkom vyššie, nesčíta sa to na limit."
        )

    pre = []
    for key, label in (
        ("under_min", f"pod {min_buy} €"),
        ("over_cap", f"nad {max_buy} €"),
        ("no_sk_delivery", "bez doručenia na SK"),
        ("not_buy_now", "nie kúpiť hneď"),
        ("invalid_price", "neplatná cena"),
        ("bulky", "rozmerné"),
        ("heavy", "ťažké"),
        ("damaged", "poškodené"),
        ("skip_keyword", "zakázané slovo"),
        ("detail_damaged", "poškodené po detaile"),
        ("detail_bulky", "rozmerné po detaile"),
        ("detail_heavy", "ťažké po detaile"),
        ("detail_skip_keyword", "zakázané slovo po detaile"),
    ):
        if n(key):
            pre.append(f"{n(key)} {label}")
    if pre:
        lines.append("- pred použiteľnými / počas detailu ešte vypadlo: " + ", ".join(pre) + ".")

    return "\n".join(lines) or "- (žiadny priebeh)"


def _format_status(run: HuntRun, *, min_profit, buy_count: int, shown: int) -> str:
    notes = _status_notes(run)
    health = []
    for market, stats in run.source_stats.items():
        health.append(
            f"- {market.value}: stiahnuté {stats.get('fetched', 0)}, "
            f"použiteľné {stats.get('usable', 0)}, ocenené {stats.get('scored', 0)}, "
            f"BUY {stats.get('buy', 0)}"
        )
    if not health:
        health.append("- žiadny marketplace neskóroval")
    scored = _funnel_n(run, "scored")
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
        f"Priebeh:\n{_format_progress(run, min_profit=min_profit)}\n\n"
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
