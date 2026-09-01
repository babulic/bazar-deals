from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.adapters.central_europe import SITES
from bazar_deals.ai_identity import AIIdentityClient
from bazar_deals.ai_review import AIReviewClient
from bazar_deals.catalog import hunt_research_only, matches_hunt_target, reject_physical
from bazar_deals.config import Settings
from bazar_deals.domain import (
    Action,
    Deal,
    IdentifiedItem,
    Listing,
    Marketplace,
    Money,
    Vertical,
)
from bazar_deals.identity import ItemSpecs, identify, identity_subject, listing_text, with_specs
from bazar_deals.progress import emit, set_phase, start_heartbeat, stop_heartbeat
from bazar_deals.rules import rules
from bazar_deals.scoring import assumed_shipping, score_deal
from bazar_deals.soldcomps import PriceBookMiss, SoldCompClient
from bazar_deals.working import is_working_listing

_HIGH_RISK_DETAIL_KINDS = {"phones", "hardware", "photo"}
_MARKETPLACE_PRIORITY = (
    Marketplace.VINTED,
    Marketplace.AUKRO,
    Marketplace.EBAY,
    Marketplace.BAZOS,
)

_FUNNEL_KEYS = (
    "fetched",
    "not_buy_now",
    "invalid_price",
    "no_sk_delivery",
    "over_cap",
    "under_min",
    "damaged",
    "bulky",
    "skip_keyword",
    "heavy",
    "oversized",
    "usable",
    "score_capped",
    "detail_failed",
    "detail_damaged",
    "detail_bulky",
    "detail_skip_keyword",
    "detail_heavy",
    "detail_oversized",
    "insufficient_detail",
    "identity_weak",
    "identity_ai_rescued",
    "identity_ai_failed",
    "sold_lookup_cap",
    "no_sold_comps",
    "asking_only_comps",
    "asking_only_provisional",
    "above_typical",
    "scored",
    "pre_ai_buy",
    "ai_reviewed",
    "ai_rejected",
    "ai_unavailable",
    "ai_review_cap",
    "ai_below_net_profit",
    "buy",
    "below_net_profit",
)


@dataclass
class HuntRun:
    """One hunt pass: scored deals plus the funnel that explains missing alerts."""

    deals: list[Deal]
    funnel: Counter[str]
    source_stats: dict[Marketplace, Counter[str]]
    fetch_notes: list[str] = field(default_factory=list)
    listings: list[Listing] = field(default_factory=list)
    price_book_misses: list[PriceBookMiss] = field(default_factory=list)


def hunt(
    source: ListingSource,
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
    reviewer: AIReviewClient | None = None,
    identifier: AIIdentityClient | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    return score_listings(
        source.fetch_new(vertical),
        settings,
        sold or SoldCompClient(settings),
        enrichers={Marketplace(source.marketplace): source},
        reviewer=reviewer,
        identifier=identifier,
    ).deals


def hunt_sources(
    sources: list[ListingSource],
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
    reviewer: AIReviewClient | None = None,
    identifier: AIIdentityClient | None = None,
    score: bool = True,
) -> HuntRun:
    settings = settings or Settings()
    listings: list[Listing] = []
    enrichers: dict[Marketplace, ListingSource] = {}
    fetch_notes: list[str] = []
    sold_client = sold or SoldCompClient(settings)
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("HUNT_HEARTBEAT"):
        start_heartbeat()
    try:
        for source in sources:
            enrichers[Marketplace(source.marketplace)] = source
            try:
                set_phase(f"{source.marketplace} fetch")
                emit(f"{source.marketplace}: fetching")
                started = time.monotonic()
                batch = source.fetch_new(vertical)
                elapsed = int(time.monotonic() - started)
                note = f"{source.marketplace}: fetched {len(batch)}"
                emit(f"{note} in {elapsed}s")
                if not is_alert_noise(note):
                    fetch_notes.append(note)
                listings.extend(batch)
                for source_note in getattr(source, "notes", []):
                    emit(source_note)
                    if source_note and not is_alert_noise(source_note):
                        fetch_notes.append(source_note)
            except (RuntimeError, httpx.HTTPError) as exc:
                note = f"{source.marketplace}: fetched 0 ({exc})"
                emit(note)
                if not is_alert_noise(note):
                    fetch_notes.append(note)
        if not score:
            return HuntRun(
                deals=[],
                funnel=Counter(fetched=len(listings)),
                source_stats={},
                fetch_notes=fetch_notes,
                listings=listings,
            )
        set_phase("scoring")
        emit(f"scoring {len(listings)} fetched listing(s)")
        run = score_listings(
            listings,
            settings,
            sold_client,
            enrichers=enrichers,
            reviewer=reviewer,
            identifier=identifier,
        )
        extra = [
            note
            for note in getattr(sold_client, "notes", [])
            if note and note not in fetch_notes and not is_alert_noise(note)
        ]
        run.fetch_notes = fetch_notes + extra
        run.listings = listings
        run.price_book_misses = list(getattr(sold_client, "misses", []) or [])
        return run
    finally:
        stop_heartbeat()
        set_phase("done")


def score_listings(
    listings: list[Listing],
    settings: Settings,
    sold: SoldCompClient,
    *,
    enrichers: dict[Marketplace, ListingSource] | None = None,
    reviewer: AIReviewClient | None = None,
    identifier: AIIdentityClient | None = None,
) -> HuntRun:
    cap = settings.max_buy_eur
    floor = settings.min_buy_eur
    enrichers = enrichers or {}
    funnel: Counter[str] = Counter()
    source_stats: dict[Marketplace, Counter[str]] = defaultdict(Counter)
    funnel["fetched"] = len(listings)
    converted = []
    for listing in listings:
        try:
            converted.append(_to_eur(listing, settings.eur_czk, settings.eur_pln))
        except ValueError:
            funnel["invalid_price"] += 1
            source_stats[listing.marketplace]["fetched"] += 1
    usable: list[Listing] = []
    for listing in converted:
        source_stats[listing.marketplace]["fetched"] += 1
        if not listing.is_immediate_buy():
            funnel["not_buy_now"] += 1
            continue
        if listing.price.amount <= 0:
            funnel["invalid_price"] += 1
            continue
        if not listing.purchase_allowed(require_confirmation=listing.marketplace is Marketplace.EBAY):
            funnel["no_sk_delivery"] += 1
            continue
        if listing.price.amount < floor:
            funnel["under_min"] += 1
            continue
        if listing.price.amount > cap:
            funnel["over_cap"] += 1
            continue
        if not is_working_listing(listing):
            funnel["damaged"] += 1
            continue
        dropped = reject_physical(f"{listing.title} {listing.description}")
        if dropped:
            funnel[dropped] += 1
            continue
        usable.append(listing)
        source_stats[listing.marketplace]["usable"] += 1
    funnel["usable"] = len(usable)
    seeder = getattr(sold, "seed_asking", None)
    if callable(seeder):
        seeder(converted)

    # Round-robin by marketplace in fetch order (not cheapest-first, which
    # filled the cap with €20 Vinted clothing). Cached-overpriced ads do not
    # consume the 80 valuation slots. Unconfirmed SK (sbazar catalog) stays last.
    ready: list[Listing] = []
    pending_sk: list[Listing] = []
    for listing in usable:
        if listing.marketplace.value in SITES and listing.ships_to_slovakia is not True:
            pending_sk.append(listing)
        else:
            ready.append(listing)
    # Targeted SKUs first so the 80-slot cap is iPhone/LEGO/Commodore, not
    # whatever showed up first in a Vinted clothing dump.
    hits = [item for item in ready if matches_hunt_target(f"{item.title} {item.search_query or ''}")]
    hit_ids = {(item.marketplace, item.external_id) for item in hits}
    rest = [item for item in ready if (item.marketplace, item.external_id) not in hit_ids]
    queue = _round_robin_listings(hits) + _round_robin_listings(rest) + pending_sk
    score_cap = int(rules()["hunt"].get("max_score_listings", 80))
    if hunt_research_only():
        score_cap = max(score_cap, 120)

    if identifier is None and settings.ai_review_enabled:
        identifier = AIIdentityClient(settings)

    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("HUNT_HEARTBEAT"):
        start_heartbeat()
    deals: list[Deal] = []
    rescues: Counter[str] = Counter()
    min_conf = float(rules()["identity"]["confidence"]["min_to_hunt"])
    brands = {str(name).casefold() for name in rules()["identity"].get("generic_brands", [])}
    peeker = getattr(sold, "cached_typical", None)
    work = 0
    try:
        for index, listing in enumerate(queue, start=1):
            if index == 1 or index % 50 == 0 or index == len(queue):
                set_phase(f"scoring {index}/{len(queue)}")
                emit(f"scoring {index}/{len(queue)} (valued {work})")

            item = identify(listing)
            words = set(re.findall(r"[a-z0-9]+", listing_text(listing).casefold()))
            if item.kind == "clothing" and not (words & brands):
                funnel["identity_weak"] += 1
                continue
            if item.confidence >= min_conf and item.search_query:
                lookup_key = with_specs(
                    item.search_query,
                    item.specs if isinstance(item.specs, ItemSpecs) else None,
                ).casefold().strip()
                cached = peeker(
                    listing,
                    query=lookup_key,
                    specs=item.specs if isinstance(item.specs, ItemSpecs) else None,
                    subject=identity_subject(item),
                ) if callable(peeker) else None
                if cached is not None and listing.price.amount >= cached.median:
                    funnel["above_typical"] += 1
                    continue

            if score_cap > 0 and work >= score_cap:
                funnel["score_capped"] = len(queue) - index + 1
                emit(f"scoring cap {score_cap} valued; {funnel['score_capped']} left")
                break
            work += 1

            enricher = enrichers.get(listing.marketplace)
            if not listing.manual_import and enricher is not None and (len(listing.description.strip()) < 40 or (
                listing.marketplace.value in SITES and listing.ships_to_slovakia is not True
            )):
                listing = enricher.enrich_listing(listing)
                if listing.raw.get("detail_fetched") is False and not listing.description.strip():
                    funnel["detail_failed"] += 1
            try:
                listing = _to_eur(listing, settings.eur_czk, settings.eur_pln)
            except ValueError:
                funnel["invalid_price"] += 1
                continue
            if not listing.purchase_allowed(require_confirmation=listing.marketplace.value in SITES):
                funnel["no_sk_delivery"] += 1
                continue
            if not listing.is_immediate_buy():
                funnel["not_buy_now"] += 1
                continue
            if not settings.min_buy_eur <= listing.price.amount <= cap:
                funnel["over_cap" if listing.price.amount > cap else "under_min"] += 1
                continue
            if not is_working_listing(listing):
                funnel["detail_damaged"] += 1
                continue
            dropped = reject_physical(f"{listing.title} {listing.description}")
            if dropped:
                funnel[f"detail_{dropped}"] += 1
                continue

            item = identify(listing)
            if item.kind in _HIGH_RISK_DETAIL_KINDS and len(listing.description.strip()) < 10:
                funnel["insufficient_detail"] += 1
                continue
            if item.confidence < min_conf or not item.search_query:
                item = _rescue_identity(listing, item, settings, identifier, funnel, rescues)
                if item is None:
                    funnel["identity_weak"] += 1
                    continue
            lookup_key = with_specs(
                item.search_query,
                item.specs if isinstance(item.specs, ItemSpecs) else None,
            ).casefold().strip()
            item = item.model_copy(update={"search_query": lookup_key, "model": lookup_key or item.model})
            comp = sold.median_sold(
                listing,
                query=lookup_key,
                specs=item.specs if isinstance(item.specs, ItemSpecs) else None,
                subject=identity_subject(item),
            )
            if comp is None:
                funnel["no_sold_comps"] += 1
                continue
            if not comp.reliable_for_buy:
                funnel["asking_only_comps"] += 1
                if not settings.ai_review_enabled or not settings.ai_review_required:
                    continue
                funnel["asking_only_provisional"] += 1
            item = item.model_copy(
                update={"asking_sample": comp.sample, "sold_label": comp.label}
            )
            typical = comp.median
            if listing.price.amount >= typical:
                funnel["above_typical"] += 1
                continue
            shipping = _shipping_eur(listing, settings)
            deal = score_deal(item, typical, shipping, settings=settings)
            funnel["scored"] += 1
            source_stats[listing.marketplace]["scored"] += 1
            if deal.action is Action.BUY:
                funnel["pre_ai_buy"] += 1
                source_stats[listing.marketplace]["pre_ai_buy"] += 1
            else:
                funnel["below_net_profit"] += 1
            deals.append(deal)
    finally:
        stop_heartbeat()

    skipped = int(getattr(sold, "live_sold_skipped", 0) or 0)
    if skipped:
        funnel["sold_lookup_cap"] = skipped

    if settings.ai_review_enabled:
        reviewer = reviewer or AIReviewClient(settings)
        deals = _apply_ai_gate(deals, settings, reviewer, funnel)

    funnel["buy"] = sum(1 for deal in deals if deal.action is Action.BUY)
    for deal in deals:
        if deal.action is Action.BUY:
            source_stats[deal.item.listing.marketplace]["buy"] += 1
    deals.sort(key=lambda deal: (deal.action is not Action.BUY, -deal.costs.net_profit))
    emit(_format_funnel(funnel))
    emit(_format_source_health(source_stats))
    return HuntRun(
        deals=deals,
        funnel=funnel,
        source_stats=source_stats,
        price_book_misses=list(getattr(sold, "misses", []) or []),
    )


def _rescue_identity(
    listing: Listing,
    item: IdentifiedItem,
    settings: Settings,
    identifier: AIIdentityClient | None,
    funnel: Counter[str],
    rescues: Counter[str],
) -> IdentifiedItem | None:
    """Ask the AI to name an item the rules could not, reading the whole ad.

    This only establishes identity. The valuation still comes from the
    stored price book and every rescued candidate must clear the same
    net-profit floor and the same fail-closed price review as any other.
    """
    if identifier is None:
        return None
    if rescues["used"] >= max(0, int(settings.ai_max_identifications)):
        return None
    rescues["used"] += 1
    try:
        rescued = identifier.apply(listing, item)
    except (RuntimeError, ValueError, httpx.HTTPError):
        funnel["identity_ai_failed"] += 1
        return None
    min_conf = float(rules()["identity"]["confidence"]["min_to_hunt"])
    if rescued is None or rescued.confidence < min_conf or not rescued.search_query:
        funnel["identity_ai_failed"] += 1
        return None
    funnel["identity_ai_rescued"] += 1
    return rescued


def _apply_ai_gate(
    deals: list[Deal],
    settings: Settings,
    reviewer: AIReviewClient,
    funnel: Counter[str],
) -> list[Deal]:
    buys = [deal for deal in deals if deal.action is Action.BUY]
    ordered_buys = _round_robin_deals(buys)
    replacements: dict[tuple[str, str], Deal] = {}
    reviewed = 0
    for deal in ordered_buys:
        key = (deal.item.listing.marketplace.value, deal.item.listing.external_id)
        if reviewed >= max(0, int(settings.ai_max_reviews)):
            funnel["ai_review_cap"] += 1
            if settings.ai_review_required:
                replacements[key] = deal.model_copy(
                    update={"action": Action.SKIP, "reason": "AI review cap reached; fail closed"}
                )
            continue
        reviewed += 1
        try:
            review = reviewer.review(deal)
        except (RuntimeError, ValueError, httpx.HTTPError) as exc:
            funnel["ai_unavailable"] += 1
            if settings.ai_review_required:
                replacements[key] = deal.model_copy(
                    update={"action": Action.SKIP, "reason": f"AI review unavailable: {exc}"}
                )
            continue
        funnel["ai_reviewed"] += 1
        if not review.approved or not review.complete_product or review.quick_sale_price_eur is None:
            funnel["ai_rejected"] += 1
            replacements[key] = deal.model_copy(
                update={
                    "action": Action.SKIP,
                    "reason": f"AI rejected candidate: {review.reason or 'identity/price not verified'}",
                    "ai_review": review,
                }
            )
            continue

        corrected_item = deal.item.model_copy(
            update={
                "canonical_name": review.canonical_name or deal.item.canonical_name,
                "kind": review.kind or deal.item.kind,
                "confidence": max(deal.item.confidence, review.confidence),
            }
        )
        # AI can only lower (never raise) the deterministic sold-P25 valuation.
        final_resale = min(deal.costs.estimated_resale, review.quick_sale_price_eur)
        corrected = score_deal(
            corrected_item,
            final_resale,
            deal.costs.shipping,
            settings=settings,
        ).model_copy(update={"ai_review": review})
        if corrected.action is not Action.BUY:
            funnel["ai_below_net_profit"] += 1
            corrected = corrected.model_copy(
                update={"reason": f"AI-corrected valuation: {corrected.reason}; {review.reason}"}
            )
        replacements[key] = corrected

    out: list[Deal] = []
    for deal in deals:
        key = (deal.item.listing.marketplace.value, deal.item.listing.external_id)
        out.append(replacements.get(key, deal))
    return out


def _round_robin_listings(listings: list[Listing]) -> list[Listing]:
    groups: dict[Marketplace, list[Listing]] = defaultdict(list)
    for listing in listings:
        groups[listing.marketplace].append(listing)
    return _round_robin_groups(groups)


def _round_robin_deals(deals: list[Deal]) -> list[Deal]:
    groups: dict[Marketplace, list[Deal]] = defaultdict(list)
    for deal in deals:
        groups[deal.item.listing.marketplace].append(deal)
    for batch in groups.values():
        batch.sort(key=lambda item: item.costs.net_profit, reverse=True)
    return _round_robin_groups(groups)


def _round_robin_groups(groups):
    order = [market for market in _MARKETPLACE_PRIORITY if groups.get(market)]
    order.extend(market for market in groups if market not in order and groups.get(market))
    out = []
    index = 0
    while True:
        added = False
        for market in order:
            batch = groups[market]
            if index < len(batch):
                out.append(batch[index])
                added = True
        if not added:
            break
        index += 1
    return out


def _shipping_eur(listing: Listing, settings: Settings) -> Decimal:
    if listing.shipping_cost is None:
        return assumed_shipping(listing.price.amount, settings)
    return listing.shipping_cost.to_eur(settings.eur_czk, eur_pln=settings.eur_pln)


def is_dry_price_book_miss(note: str) -> bool:
    return (note or "").startswith("price book: insufficient comparable ads")


def is_alert_noise(note: str) -> bool:
    """Operator diagnostics that must not appear on the Deal alerts issue."""
    text = note or ""
    if is_dry_price_book_miss(text) or text.startswith("price book:"):
        return True
    if text.startswith(("allegro_pl:", "allegro_sk:", "olx:")):
        return True
    if text.startswith("facebook:"):
        return "fetched " not in text or "fetched 0" in text
    if text.startswith("ebay:") and any(
        marker in text
        for marker in ("no-persistence", "OAuth", "skipped", "credentials are required")
    ):
        return True
    markers = (
        "LOGIN_REQUIRED",
        "ACCESS_NOT_GRANTED",
        "NEEDS_DELIVERY_CONFIRMATION",
        ": READY:",
        "manual import only",
        "BLOCKED: manual import",
        "live query budget exhausted",
    )
    return any(marker in text for marker in markers)


def _format_funnel(funnel: Counter[str]) -> str:
    parts = [f"{key}={funnel.get(key, 0)}" for key in _FUNNEL_KEYS]
    return "filter: " + " ".join(parts)


def _format_source_health(source_stats: dict[Marketplace, Counter[str]]) -> str:
    order = [market for market in _MARKETPLACE_PRIORITY if market in source_stats]
    order.extend(market for market in source_stats if market not in order)
    parts = []
    for market in order:
        stats = source_stats[market]
        parts.append(
            f"{market.value}[fetched={stats.get('fetched', 0)},usable={stats.get('usable', 0)},"
            f"scored={stats.get('scored', 0)},pre_ai_buy={stats.get('pre_ai_buy', 0)},"
            f"buy={stats.get('buy', 0)}]"
        )
    return "source-health: " + " ".join(parts)


def _to_eur(listing: Listing, eur_czk: Decimal | None, eur_pln: Decimal | None = None) -> Listing:
    updates: dict = {}
    raw = dict(listing.raw)
    if listing.price.currency.upper() != "EUR":
        raw["original_price_currency"] = listing.price.currency.upper()
        updates["price"] = Money(amount=listing.price.to_eur(eur_czk, eur_pln=eur_pln), currency="EUR")
    if listing.shipping_cost is not None and listing.shipping_cost.currency.upper() != "EUR":
        raw["original_shipping_currency"] = listing.shipping_cost.currency.upper()
        updates["shipping_cost"] = Money(
            amount=listing.shipping_cost.to_eur(eur_czk, eur_pln=eur_pln), currency="EUR"
        )
    if updates:
        updates["raw"] = raw
    return listing.model_copy(update=updates) if updates else listing
