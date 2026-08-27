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
from bazar_deals.github_alerts import GitHubIssueAlerts, select_alert_deals
from bazar_deals.notify import format_deal
from bazar_deals.pipeline import hunt_sources
from bazar_deals.soldcomps import SoldCompClient

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "bazos_rss.xml"
SOLD_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ebay_sold_1541.html"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Marketplace mispricing hunter")
    parser.add_argument("command", choices=["hunt"], help="Run the deal pipeline")
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
        help="Post the top ranked hunt cards (each with a BUY áno/nie flag) to the Deal alerts GitHub issue",
    )
    args = parser.parse_args(argv)

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
