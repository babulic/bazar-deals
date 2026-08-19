from decimal import Decimal

from bazar_deals.catalog import CATALOG_COMPS
from bazar_deals.domain import IdentifiedItem


def estimate_resale(item: IdentifiedItem) -> Decimal | None:
    hay = item.canonical_name.casefold()
    for key, (_name, eur) in CATALOG_COMPS.items():
        if key in hay or key in item.listing.title.casefold():
            return Decimal(str(eur))
    return None
