from __future__ import annotations

from decimal import Decimal

import httpx

from bazar_deals.config import Settings
from bazar_deals.domain import Action, Deal
from bazar_deals.notify import (
    format_github_deal,
    is_cheaper_than_usual,
)
from bazar_deals.pipeline import HuntRun, is_alert_noise
from bazar_deals.rules import rules
from bazar_deals.watchlist import MIN_SOLD_SAMPLE

ALERT_ISSUE_TITLE = rules()["github"]["alert_issue_title"]
ALERT_LABEL = rules()["github"]["alert_label"]
ALERT_TOP_N = int(rules()["github"]["alert_top_n"])
SELL_ALERT_ISSUE_TITLE = str(rules()["github"]["sell_alert_issue_title"])
SELL_ALERT_LABEL = str(rules()["github"]["sell_alert_label"])
_API = "https://api.github.com"


def listing_key(deal: Deal) -> str:
    listing = deal.item.listing
    return f"{listing.marketplace.value}:{listing.external_id}"


def alert_profit_floor(min_net_profit=None) -> Decimal:
    if min_net_profit is not None:
        return Decimal(str(min_net_profit))
    return Decimal(str(rules()["hunt"]["alert_min_net_profit_eur"]))


def select_alert_deals(
    deals: list[Deal],
    *,
    limit: int | None = None,
    min_net_profit=None,
) -> list[Deal]:
    """Top hunt cards whose expected net profit is at least the alert floor.

    BUY cards come first. Other scored ads at or above the floor are only
    used when HUNT_NOTIFY_PROGRESS formats a debug comment. Immediate issue
    comments use select_buy_alerts.
    """
    cap = ALERT_TOP_N if limit is None else max(0, int(limit))
    floor = alert_profit_floor(min_net_profit)
    eligible = [deal for deal in deals if deal.costs.net_profit >= floor]
    buys = [deal for deal in eligible if deal.action is Action.BUY]
    others = [deal for deal in eligible if deal.action is not Action.BUY]
    ranked_buys = sorted(
        buys,
        key=lambda deal: (deal.costs.net_profit, deal.item.confidence),
        reverse=True,
    )
    ranked_others = sorted(
        others,
        key=lambda deal: (deal.costs.net_profit, deal.item.confidence),
        reverse=True,
    )
    return (ranked_buys + ranked_others)[:cap]


def select_buy_alerts(
    deals: list[Deal],
    *,
    limit: int | None = None,
    min_net_profit=None,
) -> list[Deal]:
    """Immediate notify: BUY listings with expected net at least the alert floor."""
    cap = ALERT_TOP_N if limit is None else max(0, int(limit))
    floor = alert_profit_floor(min_net_profit)
    buys = [
        deal
        for deal in deals
        if deal.action is Action.BUY and deal.costs.net_profit >= floor
    ]
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
    min_buy=None,
    max_buy=None,
    min_alert_profit=None,
    include_progress: bool = True,
) -> str:
    """Hunt report. Pagination/fetch Priebeh is optional (debug flag)."""
    shown = select_alert_deals(run.deals, min_net_profit=min_alert_profit)
    buy_count = sum(1 for deal in run.deals if deal.action is Action.BUY)
    ping = f"@{mention}\n\n" if mention and buy_count else ""
    markers = "\n".join(f"<!-- listing:{listing_key(deal)} -->" for deal in shown)
    status = _format_status(
        run,
        min_profit=min_profit,
        min_buy=min_buy,
        max_buy=max_buy,
        buy_count=buy_count,
        shown=len(shown),
        include_progress=include_progress,
    )
    sections = [f"{ping}{markers}\n{status}" if markers else f"{ping}{status}"]
    if shown:
        sections.append("\n\n---\n\n".join(format_github_deal(deal) for deal in shown))
    return "\n\n".join(section.rstrip() for section in sections) + "\n"


def _status_notes(run: HuntRun) -> str:
    notes = [note for note in run.fetch_notes if not is_alert_noise(note)]
    return "\n".join(f"- {note}" for note in notes) or "- (no sources fetched)"


def _funnel_n(run: HuntRun, key: str) -> int:
    return int(run.funnel.get(key, 0) or 0)


def _format_progress(run: HuntRun, *, min_profit, min_buy=None, max_buy=None) -> str:
    """Slovak drop-off, only non-zero counts, with units so the numbers add up."""
    n = lambda key: _funnel_n(run, key)
    hunt = rules()["hunt"]
    score_cap = int(hunt["max_score_listings"])
    min_buy = hunt["min_buy_eur"] if min_buy is None else min_buy
    max_buy = hunt["max_buy_eur"] if max_buy is None else max_buy
    usable = n("usable")
    capped = n("score_capped")
    tried = max(0, usable - capped) if usable else 0
    lines: list[str] = []

    if run.batch_progress is not None:
        progress = run.batch_progress
        lines.append(
            f"- Dávka {progress.batch_id[:8]}: strana {progress.page}/{progress.pages}, "
            f"inzeráty {progress.start + 1}–{progress.end} z {progress.total}. "
            f"Po úspešnej strane zostáva {progress.remaining}; nový fetch sa spustí "
            "až po prejdení celej dávky."
        )

    if usable:
        if capped and run.batch_progress is not None:
            lines.append(
                f"- Strana sa nedokončila: {tried} sa skúsilo a {capped} zostáva. "
                "Offset sa neposunie a ďalší run zopakuje túto stranu."
            )
        elif capped:
            lines.append(
                f"- {usable} použiteľných inzerátov (kúpiť hneď, {min_buy}–{max_buy} €). "
                f"Ocenenie má limit {score_cap} za hunt, takže sa skúšalo {tried} "
                f"a {capped} ostalo mimo."
            )
        else:
            lines.append(
                f"- Táto strana obsahuje {usable} použiteľných inzerátov "
                f"(kúpiť hneď, {min_buy}–{max_buy} €)."
                if run.batch_progress is not None
                else f"- {usable} použiteľných inzerátov "
                f"(kúpiť hneď, {min_buy}–{max_buy} €)."
            )

    scored_bits: list[str] = []
    if n("buy"):
        scored_bits.append(f"{n('buy')} BUY áno")
    cheaper_under = 0
    for deal in run.deals:
        if deal.action is not Action.BUY and is_cheaper_than_usual(deal):
            cheaper_under += 1
    if cheaper_under:
        scored_bits.append(
            f"{cheaper_under} lacnejších ako obvyklá, pod prahom {min_profit} €"
        )
    if n("above_typical"):
        scored_bits.append(
            f"{n('above_typical')} s nákupom nad obvyklou (nie ocenené, nie deal)"
        )
    elif not cheaper_under:
        if n("below_net_profit"):
            count = n("below_net_profit")
            word = "ocenený" if count == 1 else "ocenených"
            scored_bits.append(f"{count} {word} pod prahom {min_profit} €")
        elif n("scored") and not n("buy"):
            scored_bits.append(f"{n('scored')} ocenených")
    if n("no_sold_comps"):
        scored_bits.append(
            f"{n('no_sold_comps')} inzerátov bez {MIN_SOLD_SAMPLE} "
            "porovnateľných cien rovnakého modelu (nie sú stratové)"
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
        if run.batch_progress is not None:
            lines.append(
                f"- Cenník narazil na limit pri {n('sold_lookup_cap')} produktoch. "
                "Tie ostávajú neocenené; strana sa aj tak checkpointne, aby dávka postúpila."
            )
        else:
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
        ("oversized", "nezmestí sa do krabice od topánok"),
        ("damaged", "poškodené"),
        ("skip_keyword", "zakázané slovo"),
        ("drop_kind", "mimo sortiment (príslušenstvo/oblečenie/médiá/knihy)"),
        ("excluded_product", "vyradené staré DSLR"),
        ("detail_damaged", "poškodené po detaile"),
        ("detail_bulky", "rozmerné po detaile"),
        ("detail_heavy", "ťažké po detaile"),
        ("detail_oversized", "krabica od topánok po detaile"),
        ("detail_skip_keyword", "zakázané slovo po detaile"),
        ("detail_excluded_product", "vyradené staré DSLR po detaile"),
        ("condition_unverified", "Aukro bez overeného stavu"),
    ):
        if n(key):
            pre.append(f"{n(key)} {label}")
    if pre:
        lines.append("- pred použiteľnými / počas detailu ešte vypadlo: " + ", ".join(pre) + ".")

    return "\n".join(lines) or "- (žiadny priebeh)"


def _format_status(
    run: HuntRun,
    *,
    min_profit,
    min_buy=None,
    max_buy=None,
    buy_count: int,
    shown: int,
    include_progress: bool = True,
) -> str:
    notes = _status_notes(run)
    scored = _funnel_n(run, "scored")
    above = _funnel_n(run, "above_typical")
    if buy_count:
        headline = (
            f"**{buy_count} BUY áno** · Top {shown} vyhodnotených kandidátov "
            f"(prešli aj neprešli), BUY prah {min_profit} € čistého zisku."
        )
    elif shown:
        headline = (
            f"**0 BUY áno** · Top {shown} vyhodnotených kandidátov (všetky neprešli), "
            f"BUY prah {min_profit} € čistého zisku. Karty uvádzajú odkaz, ceny, "
            "výsledok a dôvod zamietnutia."
        )
    elif scored == 0:
        if above:
            headline = (
                f"**0 BUY áno** · žiadne ocenené kandidáty. "
                f"{above} inzerátov malo nákup nad obvyklou cenou — to nie je deal "
                "a nie je to ocenenie."
            )
        else:
            headline = (
                f"**0 BUY áno** · zisk sa nerátal — usable inzeráty nie sú ocenené "
                f"(chýba trhový cenník Bazos/Aukro/Vinted / málo podobných inzerátov). "
                f"Toto nie je dôkaz, že sú stratové."
            )
    else:
        headline = (
            f"**0 BUY áno** · žiadne ziskové karty (prah {min_profit} € čistého zisku). "
            "Stratové a podprahové inzeráty sa neposielajú."
        )
    if not include_progress:
        return headline
    return (
        f"{headline}\n\n"
        f"Zdroje:\n{notes}\n\n"
        "Priebeh:\n"
        + _format_progress(
            run,
            min_profit=min_profit,
            min_buy=min_buy,
            max_buy=max_buy,
        )
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
        """Immediate BUY ≥ alert floor. Silent on 0 BUY, including empty days.

        Pagination/fetch Priebeh is omitted unless hunt_notify_progress is on.
        Duplicate BUY listings already commented on this issue are skipped.
        Near-miss SKIP cards at or above the floor are not posted.
        """
        self._require_auth()
        floor = self.settings.alert_min_net_profit_eur
        include_progress = bool(self.settings.hunt_notify_progress)
        buys = select_buy_alerts(run.deals, min_net_profit=floor)

        if buys:
            issue = self.ensure_issue()
            seen = self._seen_keys(issue)
            fresh = [deal for deal in buys if listing_key(deal) not in seen]
            if not fresh:
                return 0
            posted = HuntRun(
                deals=fresh,
                funnel=run.funnel,
                source_stats=run.source_stats,
                fetch_notes=run.fetch_notes,
                listings=run.listings,
                price_book_misses=run.price_book_misses,
                batch_progress=run.batch_progress,
            )
            body = format_hunt_comment(
                posted,
                mention=self._assignee(),
                min_profit=self.settings.min_net_profit_eur,
                min_buy=self.settings.min_buy_eur,
                max_buy=self.settings.max_buy_eur,
                min_alert_profit=floor,
                include_progress=include_progress,
            )
            self._request(
                "POST",
                f"/repos/{self.repo}/issues/{issue}/comments",
                json={"body": body},
            )
            return 1

        if include_progress:
            issue = self.ensure_issue()
            body = format_hunt_comment(
                run,
                mention=self._assignee(),
                min_profit=self.settings.min_net_profit_eur,
                min_buy=self.settings.min_buy_eur,
                max_buy=self.settings.max_buy_eur,
                min_alert_profit=floor,
                include_progress=True,
            )
            self._request(
                "POST",
                f"/repos/{self.repo}/issues/{issue}/comments",
                json={"body": body},
            )
            return 1

        return 0

    def post_buyer_digest(self, body: str, *, has_buyers: bool = True) -> int:
        """Post the sell digest only when a want-ad matched own stock."""
        if not has_buyers:
            return 0
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
        for body in self._comment_bodies(issue):
            for line in body.splitlines():
                if line.startswith("<!-- listing:") and line.endswith("-->"):
                    keys.add(line[len("<!-- listing:") : -3].strip())
        return keys

    def _comment_bodies(self, issue: int) -> list[str]:
        bodies: list[str] = []
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
                bodies.append(comment.get("body") or "")
            if len(comments) < 100:
                break
            page += 1
        return bodies

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
