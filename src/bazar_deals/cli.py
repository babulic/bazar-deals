from __future__ import annotations

import argparse
from pathlib import Path

from bazar_deals.adapters.aukro import AukroSellClient
from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.adapters.vinted import VintedProClient
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Vertical
from bazar_deals.github_alerts import GitHubIssueAlerts
from bazar_deals.notify import format_deal
from bazar_deals.pipeline import hunt

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "bazos_rss.xml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Marketplace mispricing hunter")
    parser.add_argument("command", choices=["hunt"], help="Run the deal pipeline")
    parser.add_argument(
        "--source",
        choices=["bazos", "ebay", "aukro", "vinted"],
        default="bazos",
        help="Hunt source. Vinted/Aukro catalog hunt is blocked by official API scope.",
    )
    parser.add_argument("--vertical", choices=[v.value for v in Vertical], default="retro")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use bundled RSS fixture instead of live Bazos feeds",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Post BUY/WATCH deals as comments on the Deal alerts GitHub issue",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    vertical = Vertical(args.vertical)
    if args.source == "bazos":
        source = BazosRssClient(
            settings,
            fixture_path=FIXTURE if args.offline else None,
        )
    elif args.source == "ebay":
        source = EbayBrowseClient(settings)
    elif args.source == "aukro":
        source = AukroSellClient(settings)
    else:
        source = VintedProClient(settings)
    try:
        deals = hunt(source, vertical=vertical, settings=settings)
    except RuntimeError as exc:
        print(exc)
        return 2
    actionable = [deal for deal in deals if deal.action is not Action.SKIP]
    if not actionable:
        print("No deals with a positive edge.")
        return 0
    print("\n\n".join(format_deal(deal) for deal in actionable))
    if args.notify:
        try:
            posted = GitHubIssueAlerts(settings).post_deals(actionable)
        except RuntimeError as exc:
            print(exc)
            return 2
        print(f"Posted {posted} new comment(s) to the Deal alerts issue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
