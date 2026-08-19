from decimal import Decimal

from bazar_deals.catalog import CATALOG_COMPS, match_catalog_key
from bazar_deals.domain import IdentifiedItem


def estimate_resale(item: IdentifiedItem) -> Decimal | None:
    """Only a matched catalog SKU gets a number. No default 'every iPhone is 280'."""
    hay = f"{item.listing.title} {item.listing.description}"
    key = match_catalog_key(hay)
    if not key:
        return None
    return Decimal(str(CATALOG_COMPS[key][1]))
