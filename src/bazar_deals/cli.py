from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bazar_deals.adapters.central_europe import CentralEuropeClient, HUNT_SITES, SITES
from bazar_deals.adapters.aukro import AukroHuntClient
from bazar_deals.adapters.bazos import BazosRssClient
from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.adapters.vinted import VintedHuntClient
from bazar_deals.config import Settings
from bazar_deals.fx import prepare_exchange_rates
from bazar_deals.manual_import import load_manual_offers
from bazar_deals.domain import Action, Listing, Marketplace, Vertical
from bazar_deals.github_alerts import GitHubIssueAlerts, select_alert_deals
from bazar_deals.notify import format_deal
from bazar_deals.pipeline import hunt_sources, is_alert_noise, score_listings
from bazar_deals.progress import emit
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
        choices=["hunt", "sell", "import"],
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
        choices=["all", "bazos", "ebay", "aukro", "vinted", *SITES],
        default="all",
        help="Hunt all configured marketplaces; unavailable sources are reported. eBay is excluded.",
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
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Download listings and stop. Use with --listings-out. GitHub Actions uses this so each marketplace is its own visible step.",
    )
    parser.add_argument(
        "--listings-out",
        default=None,
        help="Write fetched listings as JSON (used with --fetch-only).",
    )
    parser.add_argument(
        "--listings-in",
        action="append",
        default=[],
        help="Score previously fetched JSON instead of downloading. Repeatable.",
    )
    parser.add_argument("--manual-in", action="append", default=[], help="User-selected offers in simple JSON/CSV; repeatable. Hunt scores only these and --listings-in.")
    args = parser.parse_args(argv)
    try:
        manual = [row for path in args.manual_in for row in load_manual_offers(Path(path))]
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.command == "import":
        if not args.manual_in or not args.listings_out:
            parser.error("import requires --manual-in and --listings-out")
        if any(Path(path).resolve() == Path(args.listings_out).resolve() for path in args.manual_in):
            parser.error("input and output must differ")
        notes = [f"{item.external_id}: {'READY' if item.manual_purchase_verified() else 'NEEDS_DELIVERY_CONFIRMATION'}" for item in manual if item.buy_now]
        _dump_listings(Path(args.listings_out), manual, notes=notes)
        print(f"Imported {len(manual)} offer(s). " + "; ".join(notes))
        return 0

    settings = Settings()
    fx_notes: list[str] = []
    if (args.command == "hunt" and not args.fetch_only) or args.refresh or args.buyers:
        settings, fx_notes = prepare_exchange_rates(settings, offline=args.offline)
        for note in fx_notes:
            emit(note)

    if args.command == "sell":
        inventory = load_inventory()
        if args.refresh:
            inventory, report = refresh_inventory(inventory, collect_all(settings))
            inventory = inventory.model_copy(
                update={"collected": datetime.now(timezone.utc).date().isoformat()}
            )
            target = save_inventory(inventory)
            print(f"Refreshed {report.matched} listing(s) into {target}:", file=sys.stderr)
            print(report.summary(), file=sys.stderr)
        if args.buyers:
            digest = find_buyers(inventory, settings, manual_listings=manual, offline=args.offline) if args.manual_in or args.offline else find_buyers(inventory, settings)
            digest.notes[:0] = fx_notes
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

    vertical = Vertical(args.vertical) if args.vertical else None
    sold = SoldCompClient(settings, fixture_path=SOLD_FIXTURE) if args.offline else SoldCompClient(settings)
    if args.listings_in or args.manual_in:
        listings: list[Listing] = list(manual)
        cached_notes: list[str] = []
        for path in args.listings_in:
            loaded = _load_listings(Path(path))
            emit(f"loaded {len(loaded)} listing(s) from {path}")
            listings.extend(loaded)
            note_path = Path(path).with_suffix(".notes.json")
            if note_path.is_file():
                cached_notes.extend(json.loads(note_path.read_text(encoding="utf-8")))
        sources = _sources("all", settings, fixture=None)
        enrichers = {} if args.offline else {Marketplace(source.marketplace): source for source in sources}
        emit(f"scoring {len(listings)} cached listing(s)")
        run = score_listings(listings, settings, sold, enrichers=enrichers)
        sold_notes = [
            note for note in (getattr(sold, "notes", []) or []) if not is_alert_noise(note)
        ]
        run.fetch_notes = [
            f"loaded {len(listings)} cached listing(s)",
            *(note for note in cached_notes if not is_alert_noise(note)),
        ] + sold_notes
    else:
        sources = _sources(args.source, settings, fixture=FIXTURE if args.offline else None)
        run = hunt_sources(
            sources,
            vertical=vertical,
            settings=settings,
            sold=sold,
            score=not args.fetch_only,
        )
        if args.listings_out:
            _dump_listings(Path(args.listings_out), run.listings, notes=run.fetch_notes)
            emit(f"wrote {len(run.listings)} listing(s) to {args.listings_out}")
        if args.fetch_only:
            return 0
    run.fetch_notes[:0] = fx_notes
    deals = run.deals
    buys = [deal for deal in deals if deal.action is Action.BUY]
    if buys:
        print("\n\n".join(format_deal(deal) for deal in select_alert_deals(buys, limit=len(buys))))
    else:
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
    if name in SITES:
        return [CentralEuropeClient(name, settings)]
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
    return [bazos, aukro, vinted, *(CentralEuropeClient(name, settings) for name in HUNT_SITES)]


def _dump_listings(path: Path, listings: list[Listing], *, notes: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([item.model_dump(mode="json") for item in listings], ensure_ascii=False),
        encoding="utf-8",
    )

    path.with_suffix(".notes.json").write_text(json.dumps(notes or [], ensure_ascii=False), encoding="utf-8")


def _load_listings(path: Path) -> list[Listing]:
    if not path.is_file():
        emit(f"{path} is missing; treating as 0 listings")
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [Listing.model_validate(item) for item in payload]


if __name__ == "__main__":
    raise SystemExit(main())
