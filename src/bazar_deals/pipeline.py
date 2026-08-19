from decimal import Decimal

from bazar_deals.adapters.base import ListingSource
from bazar_deals.catalog import is_bulky
from bazar_deals.config import Settings
from bazar_deals.domain import Deal, Listing, Money, Vertical
from bazar_deals.identity import identify
from bazar_deals.scoring import score_deal
from bazar_deals.valuation import estimate_resale


def hunt(
    source: ListingSource,
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
) -> list[Deal]:
    settings = settings or Settings()
    deals: list[Deal] = []
    for listing in source.fetch_new(vertical):
        listing = _to_eur(listing, settings.eur_czk)
        if not listing.is_immediate_buy():
            continue
        if listing.price.amount <= 0:
            continue
        if is_bulky(f"{listing.title} {listing.description}"):
            continue
        item = identify(listing, vertical)
        resale = estimate_resale(item)
        if resale is None:
            continue
        deals.append(
            score_deal(
                item,
                resale,
                settings.default_shipping_eur,
                min_net_profit=settings.min_net_profit_eur,
                min_margin=settings.min_margin,
            )
        )
    deals.sort(key=lambda deal: deal.costs.net_profit, reverse=True)
    return deals


def hunt_sources(
    sources: list[ListingSource],
    *,
    vertical: Vertical | None = None,
    settings: Settings | None = None,
) -> list[Deal]:
    deals: list[Deal] = []
    for source in sources:
        try:
            deals.extend(hunt(source, vertical=vertical, settings=settings))
        except RuntimeError as exc:
            print(f"{source.marketplace}: {exc}")
    deals.sort(key=lambda deal: deal.costs.net_profit, reverse=True)
    return deals


def _to_eur(listing: Listing, eur_czk: Decimal) -> Listing:
    if listing.price.currency.upper() == "EUR":
        return listing
    return listing.model_copy(
        update={"price": Money(amount=listing.price.to_eur(eur_czk), currency="EUR")}
    )
