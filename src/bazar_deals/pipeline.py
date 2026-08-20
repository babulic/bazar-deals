from __future__ import annotations

from collections import Counter
from decimal import Decimal

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import is_bulky
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Deal, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.rules import rules
from bazar_deals.scoring import assumed_shipping, score_deal
from bazar_deals.soldcomps import SoldCompClient
from bazar_deals.working import is_working_listing

_FUNNEL_KEYS = (
    "fetched",
    "not_buy_now",
    "no_sk_delivery",
    "over_cap",
    "under_min",
    "damaged",
    "bulky",
    "usable",
    "identity_weak",
    "sold_lookup_cap",
    "no_sold_comps",
    "asking_only_comps",
    "scored",
    "buy",
    "below_net_profit",
)


def hunt(
    source: ListingSource,
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    return score_listings(
        source.fetch_new(vertical),
        settings,
        sold or SoldCompClient(settings),
    )


def hunt_sources(
    sources: list[ListingSource],
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
    sold: SoldCompClient | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    listings: list[Listing] = []
    for source in sources:
        try:
            batch = source.fetch_new(vertical)
            print(f"{source.marketplace}: fetched {len(batch)}")
            listings.extend(batch)
        except RuntimeError as exc:
            print(f"{source.marketplace}: fetched 0 ({exc})")
    return score_listings(listings, settings, sold or SoldCompClient(settings))


def score_listings(
    listings: list[Listing],
    settings: Settings,
    sold: SoldCompClient,
) -> list[Deal]:
    cap = settings.max_buy_eur
    floor = settings.min_buy_eur
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
    usable.sort(key=lambda item: item.price.amount)

    deals: list[Deal] = []
    lookups = 0
    lookup_cap = int(rules()["hunt"]["max_sold_lookups"])
    min_conf = float(rules()["identity"]["confidence"]["min_to_hunt"])
    for listing in usable:
        item = identify(listing)
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
            funnel["buy"] += 1
        else:
            funnel["below_net_profit"] += 1
        deals.append(deal)
    deals.sort(key=lambda deal: (deal.action is not Action.BUY, -deal.costs.net_profit))
    print(_format_funnel(funnel))
    return deals


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
