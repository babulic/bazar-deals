from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.ai_review import AIReviewClient
from bazar_deals.catalog import is_bulky
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Deal, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import identify
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
    "sold_lookup_cap",
    "no_sold_comps",
    "asking_only_comps",
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


def hunt(
    source: ListingSource,
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
    reviewer: AIReviewClient | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    return score_listings(
        source.fetch_new(vertical),
        settings,
        sold or SoldCompClient(settings),
        enrichers={Marketplace(source.marketplace): source},
        reviewer=reviewer,
    )


def hunt_sources(
    sources: list[ListingSource],
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
    reviewer: AIReviewClient | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    listings: list[Listing] = []
    enrichers: dict[Marketplace, ListingSource] = {}
    for source in sources:
        enrichers[Marketplace(source.marketplace)] = source
        try:
            batch = source.fetch_new(vertical)
            print(f"{source.marketplace}: fetched {len(batch)}")
            listings.extend(batch)
        except (RuntimeError, httpx.HTTPError) as exc:
            print(f"{source.marketplace}: fetched 0 ({exc})")
    return score_listings(
        listings,
        settings,
        sold or SoldCompClient(settings),
        enrichers=enrichers,
        reviewer=reviewer,
    )


def score_listings(
    listings: list[Listing],
    settings: Settings,
    sold: SoldCompClient,
    *,
    enrichers: dict[Marketplace, ListingSource] | None = None,
    reviewer: AIReviewClient | None = None,
) -> list[Deal]:
    if settings.ai_review_enabled and settings.ai_review_required and not settings.openai_api_key:
        raise RuntimeError("AI_REVIEW_REQUIRED is true but OPENAI_API_KEY is missing")

    cap = settings.max_buy_eur
    floor = settings.min_buy_eur
    enrichers = enrichers or {}
    funnel: Counter[str] = Counter()
    funnel["fetched"] = len(listings)
    usable: list[Listing] = []
    for listing in listings:
        listing = _to_eur(listing, settings.eur_czk)
        if not listing.is_immediate_buy() or listing.price.amount <= 0:
            funnel["not_buy_now"] += 1
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
    funnel["usable"] = len(usable)

    # Do not let a large/cheap Bazos batch consume the global sold lookup budget.
    # Every active marketplace gets one turn per round, with Vinted/Aukro/eBay
    # deliberately placed before Bazos in each round.
    usable = _round_robin_listings(usable)

    deals: list[Deal] = []
    lookups = 0
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
            funnel["identity_weak"] += 1
            continue
        if lookups >= lookup_cap:
            funnel["sold_lookup_cap"] += 1
            continue
        lookups += 1
        comp = sold.median_sold(listing)
        if comp is None:
            funnel["no_sold_comps"] += 1
            continue
        if not comp.reliable_for_buy:
            funnel["asking_only_comps"] += 1
            continue
        item = item.model_copy(
            update={"asking_sample": comp.sample, "sold_label": comp.label}
        )
        shipping = _shipping_eur(listing, settings)
        deal = score_deal(item, comp.median, shipping, settings=settings)
        funnel["scored"] += 1
        if deal.action is Action.BUY:
            funnel["pre_ai_buy"] += 1
        else:
            funnel["below_net_profit"] += 1
        deals.append(deal)

    if settings.ai_review_enabled:
        reviewer = reviewer or AIReviewClient(settings)
        deals = _apply_ai_gate(deals, settings, reviewer, funnel)

    funnel["buy"] = sum(1 for deal in deals if deal.action is Action.BUY)
    deals.sort(key=lambda deal: (deal.action is not Action.BUY, -deal.costs.net_profit))
    print(_format_funnel(funnel))
    return deals


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
        except (RuntimeError, ValueError, httpx.HTTPError, json_error_types()) as exc:
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


def json_error_types():
    # Kept as a helper so tests can exercise malformed AI responses without
    # importing json in the hot pipeline module.
    import json

    return json.JSONDecodeError


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


def _to_eur(listing: Listing, eur_czk: Decimal) -> Listing:
    updates: dict = {}
    if listing.price.currency.upper() != "EUR":
        updates["price"] = Money(amount=listing.price.to_eur(eur_czk), currency="EUR")
    if listing.shipping_cost is not None and listing.shipping_cost.currency.upper() != "EUR":
        updates["shipping_cost"] = Money(
            amount=listing.shipping_cost.to_eur(eur_czk), currency="EUR"
        )
    return listing.model_copy(update=updates) if updates else listing
