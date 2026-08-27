from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.ai_identity import AIIdentityClient
from bazar_deals.ai_review import AIReviewClient
from bazar_deals.catalog import is_bulky
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
from bazar_deals.identity import ItemSpecs, identify, identity_subject, with_specs
from bazar_deals.rules import rules
from bazar_deals.scoring import assumed_shipping, score_deal
from bazar_deals.soldcomps import SoldCompClient
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
    "usable",
    "detail_failed",
    "detail_damaged",
    "detail_bulky",
    "insufficient_detail",
    "identity_weak",
    "identity_ai_rescued",
    "identity_ai_failed",
    "sold_lookup_cap",
    "no_sold_comps",
    "asking_only_comps",
    "asking_only_provisional",
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
) -> HuntRun:
    settings = settings or Settings()
    listings: list[Listing] = []
    enrichers: dict[Marketplace, ListingSource] = {}
    fetch_notes: list[str] = []
    for source in sources:
        enrichers[Marketplace(source.marketplace)] = source
        try:
            if (
                Marketplace(source.marketplace) is Marketplace.EBAY
                and not (settings.ebay_client_id and settings.ebay_client_secret)
            ):
                note = (
                    f"{source.marketplace}: fetched 0 "
                    "(set GitHub Actions secrets EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)"
                )
                print(note)
                fetch_notes.append(note)
                continue
            batch = source.fetch_new(vertical)
            note = f"{source.marketplace}: fetched {len(batch)}"
            print(note)
            fetch_notes.append(note)
            listings.extend(batch)
        except (RuntimeError, httpx.HTTPError) as exc:
            note = f"{source.marketplace}: fetched 0 ({exc})"
            print(note)
            fetch_notes.append(note)
    run = score_listings(
        listings,
        settings,
        sold or SoldCompClient(settings),
        enrichers=enrichers,
        reviewer=reviewer,
        identifier=identifier,
    )
    run.fetch_notes = fetch_notes
    return run


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
    usable: list[Listing] = []
    for listing in listings:
        source_stats[listing.marketplace]["fetched"] += 1
        listing = _to_eur(listing, settings.eur_czk)
        if not listing.is_immediate_buy():
            funnel["not_buy_now"] += 1
            continue
        if listing.price.amount <= 0:
            funnel["invalid_price"] += 1
            continue
        if listing.marketplace is Marketplace.EBAY and listing.ships_to_slovakia is not True:
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
        if is_bulky(f"{listing.title} {listing.description}"):
            funnel["bulky"] += 1
            continue
        usable.append(listing)
        source_stats[listing.marketplace]["usable"] += 1
    funnel["usable"] = len(usable)

    # Do not let a large/cheap Bazos batch consume the global sold lookup budget.
    # Every active marketplace gets one turn per round, with Vinted/Aukro/eBay
    # deliberately placed before Bazos in each round.
    usable = _round_robin_listings(usable)

    if identifier is None and settings.ai_review_enabled:
        identifier = AIIdentityClient(settings)

    deals: list[Deal] = []
    rescues: Counter[str] = Counter()
    lookup_queries: set[str] = set()
    lookup_cap = int(rules()["hunt"]["max_sold_lookups"])
    min_conf = float(rules()["identity"]["confidence"]["min_to_hunt"])
    for listing in usable:
        enricher = enrichers.get(listing.marketplace)
        if enricher is not None:
            listing = enricher.enrich_listing(listing)
            if listing.raw.get("detail_fetched") is False and not listing.description.strip():
                funnel["detail_failed"] += 1
        if not is_working_listing(listing):
            funnel["detail_damaged"] += 1
            continue
        if is_bulky(f"{listing.title} {listing.description}"):
            funnel["detail_bulky"] += 1
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
        if lookup_key not in lookup_queries:
            if len(lookup_queries) >= lookup_cap:
                funnel["sold_lookup_cap"] += 1
                continue
            lookup_queries.add(lookup_key)
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
            # Asking prices are allowed only as a deliberately haircutted
            # provisional valuation. They can reach BUY only through the
            # mandatory fail-closed AI web-verification gate.
            if not settings.ai_review_enabled or not settings.ai_review_required:
                continue
            funnel["asking_only_provisional"] += 1
        item = item.model_copy(
            update={"asking_sample": comp.sample, "sold_label": comp.label}
        )
        shipping = _shipping_eur(listing, settings)
        deal = score_deal(item, comp.median, shipping, settings=settings)
        funnel["scored"] += 1
        source_stats[listing.marketplace]["scored"] += 1
        if deal.action is Action.BUY:
            funnel["pre_ai_buy"] += 1
            source_stats[listing.marketplace]["pre_ai_buy"] += 1
        else:
            funnel["below_net_profit"] += 1
        deals.append(deal)

    if settings.ai_review_enabled:
        reviewer = reviewer or AIReviewClient(settings)
        deals = _apply_ai_gate(deals, settings, reviewer, funnel)

    funnel["buy"] = sum(1 for deal in deals if deal.action is Action.BUY)
    for deal in deals:
        if deal.action is Action.BUY:
            source_stats[deal.item.listing.marketplace]["buy"] += 1
    deals.sort(key=lambda deal: (deal.action is not Action.BUY, -deal.costs.net_profit))
    print(_format_funnel(funnel))
    print(_format_source_health(source_stats))
    return HuntRun(deals=deals, funnel=funnel, source_stats=source_stats)


def _rescue_identity(
    listing: Listing,
    item: IdentifiedItem,
    settings: Settings,
    identifier: AIIdentityClient | None,
    funnel: Counter[str],
    rescues: Counter[str],
) -> IdentifiedItem | None:
    """Ask the AI to name an item the rules could not, reading the whole ad.

    This only establishes identity. The valuation still comes from completed
    sales and every rescued candidate must clear the same net-profit floor and
    the same fail-closed price review as any other.
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
    for batch in groups.values():
        batch.sort(key=lambda item: item.price.amount)
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
    return listing.shipping_cost.to_eur(settings.eur_czk)


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


def _to_eur(listing: Listing, eur_czk: Decimal) -> Listing:
    updates: dict = {}
    if listing.price.currency.upper() != "EUR":
        updates["price"] = Money(amount=listing.price.to_eur(eur_czk), currency="EUR")
    if listing.shipping_cost is not None and listing.shipping_cost.currency.upper() != "EUR":
        updates["shipping_cost"] = Money(
            amount=listing.shipping_cost.to_eur(eur_czk), currency="EUR"
        )
    return listing.model_copy(update=updates) if updates else listing
