from __future__ import annotations

from decimal import Decimal

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import is_bulky
from bazar_deals.config import Settings
from bazar_deals.domain import Deal, Listing, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.scoring import score_deal
from bazar_deals.soldcomps import SoldCompClient
from bazar_deals.watchlist import MAX_BUY_EUR, MAX_SOLD_LOOKUPS


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
            listings.extend(source.fetch_new(vertical))
        except RuntimeError as exc:
            print(f"{source.marketplace}: {exc}")
    return score_listings(listings, settings, sold or SoldCompClient(settings))


def score_listings(
    listings: list[Listing],
    settings: Settings,
    sold: SoldCompClient,
) -> list[Deal]:
    cap = settings.max_buy_eur
    usable: list[Listing] = []
    for listing in listings:
        listing = _to_eur(listing, settings.eur_czk)
        if not listing.is_immediate_buy() or listing.price.amount <= 0:
            continue
        if listing.price.amount > cap:
            continue
        if is_bulky(f"{listing.title} {listing.description}"):
            continue
        usable.append(listing)
    usable.sort(key=lambda item: item.price.amount)

    deals: list[Deal] = []
    lookups = 0
    for listing in usable:
        item = identify(listing)
        if item.confidence < 0.5 or not item.search_query:
            continue
        if lookups >= MAX_SOLD_LOOKUPS:
            break
        lookups += 1
        comp = sold.median_sold(listing)
        if comp is None:
            continue
        item = item.model_copy(
            update={"asking_sample": comp.sample, "sold_label": comp.label}
        )
        deals.append(
            score_deal(
                item,
                comp.median,
                settings.default_shipping_eur,
                min_net_profit=settings.min_net_profit_eur,
                min_margin=settings.min_margin,
            )
        )
    deals.sort(key=lambda deal: deal.costs.net_profit, reverse=True)
    return deals


def _to_eur(listing: Listing, eur_czk: Decimal) -> Listing:
    if listing.price.currency.upper() == "EUR":
        return listing
    return listing.model_copy(
        update={"price": Money(amount=listing.price.to_eur(eur_czk), currency="EUR")}
    )
