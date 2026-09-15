"""Hunt numeric limits — values live in data/config.yaml."""

from decimal import Decimal

from bazar_deals.rules import rules

_HUNT = rules()["hunt"]
_FEES = rules()["fees"]
_AI = rules()["ai"]
_SELLING = rules()["selling"]


def _dec(mapping: dict, key: str) -> Decimal:
    return Decimal(str(mapping[key]))


MIN_SOLD_SAMPLE = int(_HUNT["min_sold_sample"])
MIN_SOLD_SAMPLE_CAP = int(_HUNT["min_sold_sample_cap"])
P25_FACTOR = _dec(_HUNT, "p25_factor")
MAX_SOLD_LOOKUPS = int(_HUNT["max_sold_lookups"])
MAX_SCORE_LISTINGS = int(_HUNT["max_score_listings"])
RESEARCH_SCORE_LISTINGS = int(_HUNT["research_score_listings"])
MAX_SCORE_LISTINGS_CAP = int(_HUNT["max_score_listings_cap"])
BATCH_PAGE_SIZE = int(_HUNT["batch_page_size"])
MAX_BATCH_LISTINGS = int(_HUNT["max_batch_listings"])
MAX_BATCH_NOTE_CHARS = int(_HUNT["max_batch_note_chars"])
COMPS_PRICE_MULTIPLE = _dec(_HUNT, "comps_price_multiple")
LIVE_SEARCH_SECONDS = float(_HUNT["live_search_seconds"])
HTTP_TIMEOUT_SECONDS = float(_HUNT["http_timeout_seconds"])
MAX_SCORE_SECONDS = int(_HUNT["max_score_seconds"])
SCORE_HEARTBEAT_EVERY = int(_HUNT["score_heartbeat_every"])
FX_MAX_AGE_DAYS = int(_HUNT["fx_max_age_days"])
FX_MAX_AGE_DAYS_CAP = int(_HUNT["fx_max_age_days_cap"])
COMPS_TTL_DAYS = int(_HUNT["comps_ttl_days"])
COMPS_DB = str(_HUNT["comps_db"])
FX_CACHE = str(_HUNT["fx_cache"])
HUNT_BATCH_DB = str(_HUNT["hunt_batch_db"])
MIN_NET_PROFIT_EUR = _dec(_HUNT, "min_net_profit_eur")
RESALE_FEE_RATE = _dec(_FEES, "resale_fee_rate")
SELLER_RISK_RESERVE_RATE = _dec(_FEES, "seller_risk_reserve_rate")
FX_FEE_RATE = _dec(_FEES, "fx_fee_rate")
NO_BOX_HAIRCUT_EUR = _dec(_HUNT, "no_box_haircut_eur")
MIN_BATTERY_HEALTH_PERCENT = int(_HUNT["min_battery_health_percent"])
BATTERY_UNDER_PCT = int(_HUNT["battery_under_pct"])
BATTERY_MID_PCT = int(_HUNT["battery_mid_pct"])
BATTERY_GOOD_PCT = int(_HUNT["battery_good_pct"])
BATTERY_UNDER_80_HAIRCUT_RATE = _dec(_HUNT, "battery_under_80_haircut_rate")
BATTERY_80_84_HAIRCUT_RATE = _dec(_HUNT, "battery_80_84_haircut_rate")
BATTERY_85_89_HAIRCUT_RATE = _dec(_HUNT, "battery_85_89_haircut_rate")
AI_PROVIDER = str(_AI["provider"])
AI_MAX_REVIEWS = int(_AI["max_reviews"])
AI_MAX_IDENTIFICATIONS = int(_AI["max_identifications"])
AI_REVIEW_TTL_DAYS = int(_AI["review_ttl_days"])
AI_MIN_CONFIDENCE = float(_AI["min_confidence"])
AI_TIMEOUT_SECONDS = float(_AI["timeout_seconds"])
OPENAI_BASE_URL = str(_AI["openai_base_url"])
CATAWIKI_FLOOR_EUR = _dec(_SELLING, "catawiki_floor_eur")
