from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bazar_deals.adapters.aukro import AukroHuntClient
from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.adapters.vinted import VintedHuntClient
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Vertical
from bazar_deals.github_alerts import GitHubIssueAlerts
from bazar_deals.notify import format_deal
from bazar_deals.pipeline import hunt_sources
from bazar_deals.selling.inventory import known_segments
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
        help="hunt: buy-side deal pipeline. sell: cross-border plan for own stock.",
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
        "--source",
        choices=["all", "bazos", "ebay", "aukro", "vinted"],
        default="all",
        help="Hunt Bazos + eBay + Aukro + Vinted public listings (default: all).",
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
        help="Post BUY deals meeting the net-profit floor to the Deal alerts GitHub issue",
    )
    args = parser.parse_args(argv)

    if args.command == "sell":
        plan = build_plan()
        renderer = format_json if args.format == "json" else format_markdown
        print(renderer(plan, segment=args.segment))
        return 0

    settings = Settings()
    vertical = Vertical(args.vertical) if args.vertical else None
    sources = _sources(args.source, settings, fixture=FIXTURE if args.offline else None)
    sold = SoldCompClient(settings, fixture_path=SOLD_FIXTURE) if args.offline else SoldCompClient(settings)
    deals = hunt_sources(sources, vertical=vertical, settings=settings, sold=sold)
    actionable = [deal for deal in deals if deal.action is Action.BUY]
    if not actionable:
        print(f"No deals with expected net profit >= {settings.min_net_profit_eur} EUR.")
        return 0
    print("\n\n".join(format_deal(deal) for deal in actionable))
    if args.notify:
        try:
            posted = GitHubIssueAlerts(settings).post_deals(actionable)
        except RuntimeError as exc:
            print(exc)
            return 2
        print(f"Posted {posted} hunt comment(s) to the Deal alerts issue.")
    return 0


def _sources(name: str, settings: Settings, *, fixture: Path | None):
    bazos = BazosRssClient(settings, fixture_path=fixture)
    ebay = EbayBrowseClient(settings)
    aukro = AukroHuntClient(settings)
    vinted = VintedHuntClient(settings)
    if name == "bazos":
        return [bazos]
    if name == "ebay":
        return [ebay]
    if name == "aukro":
        return [aukro]
    if name == "vinted":
        return [vinted]
    if fixture is not None:
        return [bazos]
    return [bazos, ebay, aukro, vinted]


if __name__ == "__main__":
    raise SystemExit(main())
