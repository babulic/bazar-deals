"""Hunt numeric limits — values live in data/bazar.yaml."""

from bazar_deals.rules import rules

MIN_SOLD_SAMPLE = int(rules()["hunt"]["min_sold_sample"])
MAX_SOLD_LOOKUPS = int(rules()["hunt"]["max_sold_lookups"])
