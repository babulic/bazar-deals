"""Hunt numeric limits — values live in data/bazar.yaml."""

from decimal import Decimal

from bazar_deals.rules import rules

_HUNT = rules()["hunt"]

MIN_SOLD_SAMPLE = int(_HUNT["min_sold_sample"])
P25_FACTOR = Decimal(str(_HUNT["p25_factor"]))
MAX_SOLD_LOOKUPS = int(_HUNT["max_sold_lookups"])
