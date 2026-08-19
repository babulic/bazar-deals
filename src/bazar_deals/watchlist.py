"""Hunt policy: any small shippable buy-now listing up to 60 EUR.

Not a SKU catalog. Typical value comes from eBay.de *sold* comps, not this file.
"""

from decimal import Decimal

MAX_BUY_EUR = Decimal("60")
MIN_SOLD_SAMPLE = 5
MAX_SOLD_LOOKUPS = 40
