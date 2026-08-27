from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from bazar_deals.adapters.aukro import AukroHuntClient
from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.adapters.vinted import VintedHuntClient
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Vertical
from bazar_deals.github_alerts import GitHubIssueAlerts, select_alert_deals
from bazar_deals.notify import format_deal
from bazar_deals.pipeline import hunt_sources
from bazar_deals.selling.collect import collect_all, refresh_inventory
from bazar_deals.selling.demand import find_buyers, format_buyer_digest
from bazar_deals.selling.inventory import known_segments, load_inventory, save_inventory
from bazar_deals.selling.plan import build_plan
from bazar_deals.selling.report import format_json, format_markdown
from bazar_deals.soldcomps import SoldCompClient

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "bazos_rss.xml"
SOLD_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ebay_sold_1541.html"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Marketplace mispricing hunter")
    parser.add_argument(
        "command",
        choices=["hunt", "sell"],
        help="hunt: buy-side deal pipeline. sell: own-stock plan and buyer-demand digest.",
    )
    parser.add_argument(
        "--segment",
        choices=known_segments(),
        default=None,
        help="Restrict the sell plan to one inventory segment.",
    )
    parser.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="Sell plan output format (default: md).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Page through every seller account first and refresh the inventory snapshot.",
    )
    parser.add_argument(
        "--buyers",
        action="store_true",
        help="Search European I-will-buy ads (kúpim/koupím/kaufe/kupię/veszek/compro/achète/koop) and pair them with own stock",
    )
    parser.add_argument(
        "--source",
        choices=["all", "bazos", "ebay", "aukro", "vinted"],
        default="all",
        help="Hunt Bazos + Aukro + Vinted (default: all). eBay is not a purchase source.",
    )
    parser.add_argument(
        "--vertical",
        choices=[v.value for v in Vertical],
        default=None,
        help="Optional niche filter. Default: all small shippable goods.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use bundled RSS fixture instead of live Bazos feeds",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Post hunt BUY cards, or with sell --buyers the buyer digest, to GitHub issues",
    )
    args = parser.parse_args(argv)

    if args.command == "sell":
        inventory = load_inventory()
        if args.refresh:
            settings = Settings()
            inventory, report = refresh_inventory(inventory, collect_all(settings))
            inventory = inventory.model_copy(
                update={"collected": datetime.now(timezone.utc).date().isoformat()}
            )
            target = save_inventory(inventory)
            print(f"Refreshed {report.matched} listing(s) into {target}:", file=sys.stderr)
            print(report.summary(), file=sys.stderr)
        if args.buyers:
            settings = Settings()
            digest = find_buyers(inventory, settings)
            body = format_buyer_digest(digest, mention=settings.github_assignee)
            print(body)
            if args.notify:
                try:
                    posted = GitHubIssueAlerts.for_sell_buyers(settings).post_buyer_digest(body)
                except RuntimeError as exc:
                    print(exc)
                    return 2
                print(f"Posted {posted} sell-buyer comment(s) to the Sell buyers issue.")
            return 0
        plan = build_plan(inventory)
        renderer = format_json if args.format == "json" else format_markdown
        print(renderer(plan, segment=args.segment))
        return 0

    settings = Settings()
    vertical = Vertical(args.vertical) if args.vertical else None
    sources = _sources(args.source, settings, fixture=FIXTURE if args.offline else None)
    sold = SoldCompClient(settings, fixture_path=SOLD_FIXTURE) if args.offline else SoldCompClient(settings)
    run = hunt_sources(sources, vertical=vertical, settings=settings, sold=sold)
    deals = run.deals
    shown = select_alert_deals(deals)
    actionable = [deal for deal in deals if deal.action is Action.BUY]
    if shown:
        print("\n\n".join(format_deal(deal) for deal in shown))
    if not actionable:
        print(f"No deals with expected net profit >= {settings.min_net_profit_eur} EUR.")
    if args.notify:
        try:
            posted = GitHubIssueAlerts(settings).post_run(run)
        except RuntimeError as exc:
            print(exc)
            return 2
        print(f"Posted {posted} hunt comment(s) to the Deal alerts issue.")
    return 0


def _sources(name: str, settings: Settings, *, fixture: Path | None):
    bazos = BazosRssClient(settings, fixture_path=fixture)
    aukro = AukroHuntClient(settings)
    vinted = VintedHuntClient(settings)
    if name == "bazos":
        return [bazos]
    if name == "ebay":
        return [EbayBrowseClient(settings)]
    if name == "aukro":
        return [aukro]
    if name == "vinted":
        return [vinted]
    if fixture is not None:
        return [bazos]
    return [bazos, aukro, vinted]


if __name__ == "__main__":
    raise SystemExit(main())
