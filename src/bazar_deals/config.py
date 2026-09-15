from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bazar_deals.rules import rules
from bazar_deals.watchlist import (
    AI_MAX_IDENTIFICATIONS,
    AI_MAX_REVIEWS,
    AI_MIN_CONFIDENCE,
    AI_PROVIDER,
    AI_REVIEW_TTL_DAYS,
    AI_TIMEOUT_SECONDS,
    BATCH_PAGE_SIZE,
    BATTERY_80_84_HAIRCUT_RATE,
    BATTERY_85_89_HAIRCUT_RATE,
    BATTERY_UNDER_80_HAIRCUT_RATE,
    COMPS_DB,
    COMPS_PRICE_MULTIPLE,
    COMPS_TTL_DAYS,
    FX_CACHE,
    FX_FEE_RATE,
    FX_MAX_AGE_DAYS,
    FX_MAX_AGE_DAYS_CAP,
    HTTP_TIMEOUT_SECONDS,
    HUNT_BATCH_DB,
    LIVE_SEARCH_SECONDS,
    MAX_SCORE_LISTINGS_CAP,
    MAX_SCORE_SECONDS,
    MAX_SOLD_LOOKUPS,
    MIN_NET_PROFIT_EUR,
    MIN_SOLD_SAMPLE,
    MIN_SOLD_SAMPLE_CAP,
    NO_BOX_HAIRCUT_EUR,
    OPENAI_BASE_URL,
    P25_FACTOR,
    RESALE_FEE_RATE,
    SELLER_RISK_RESERVE_RATE,
)

_HUNT = rules()["hunt"]
_FEES = rules()["fees"]
_GITHUB = rules()["github"]
_EBAY = rules()["ebay"]
_AI = rules()["ai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ebay_client_id: str = ""
    ebay_client_secret: str = Field(default="", repr=False)
    # Keep disabled while the keyset has the no-data-persistence exemption.
    # Only the isolated ebay_probe module may access eBay in this mode.
    ebay_retention_enabled: bool = False
    ebay_marketplace: str = str(_EBAY["marketplace_id"])
    ebay_campaign_id: str = ""

    aukro_api_token: str = ""
    allegro_access_token: str = Field(default="", repr=False)
    allegro_client_id: str = ""
    allegro_client_secret: str = Field(default="", repr=False)
    allegro_listing_access_confirmed: bool = False
    # Explicit overrides; otherwise the online CLI resolves a dated ECB snapshot.
    eur_pln: Decimal | None = Field(default=None, gt=0)

    vinted_access_key: str = ""
    vinted_signing_key: str = ""

    # AI review: scheduled GitHub Actions uses Copilot CLI with GITHUB_TOKEN,
    # while OPENAI_API_KEY remains an optional local/alternate provider.
    ai_provider: str = AI_PROVIDER  # auto | copilot | openai
    # Copilot Free/Student allow auto selection only. Paid seats may override
    # this with a specific model through COPILOT_MODEL.
    copilot_model: str = str(_AI["copilot_model"])
    openai_api_key: str = ""
    openai_base_url: str = OPENAI_BASE_URL
    openai_model: str = str(_AI["openai_model"])
    ai_review_enabled: bool = False
    ai_review_required: bool = False
    ai_max_reviews: int = AI_MAX_REVIEWS
    # Copilot Free has a request budget, so AI identification is capped too.
    ai_max_identifications: int = AI_MAX_IDENTIFICATIONS
    ai_review_ttl_days: int = AI_REVIEW_TTL_DAYS
    ai_min_confidence: float = AI_MIN_CONFIDENCE
    ai_timeout_seconds: float = AI_TIMEOUT_SECONDS

    telegram_bot_token: str = ""
    telegram_chat_retro: str = ""
    telegram_chat_mineral: str = ""
    telegram_chat_apple: str = ""
    telegram_chat_network: str = ""

    eur_czk: Decimal | None = Field(default=None, gt=0)
    fx_cache: str = FX_CACHE
    fx_max_age_days: int = Field(default=FX_MAX_AGE_DAYS, ge=0, le=FX_MAX_AGE_DAYS_CAP)
    fx_fee_rate: Decimal = Field(default=FX_FEE_RATE, ge=0, lt=1)
    min_net_profit_eur: Decimal = MIN_NET_PROFIT_EUR
    alert_min_net_profit_eur: Decimal = Decimal(str(_HUNT["alert_min_net_profit_eur"]))
    min_margin: Decimal = Decimal(str(_HUNT["min_margin"]))
    default_shipping_eur: Decimal = Decimal(str(_HUNT["default_shipping_eur"]))
    max_shipping_eur: Decimal = Decimal(str(_HUNT["max_shipping_eur"]))
    cheap_buy_eur: Decimal = Decimal(str(_HUNT["cheap_buy_eur"]))
    max_shipping_cheap_eur: Decimal = Decimal(str(_HUNT["max_shipping_cheap_eur"]))
    max_buy_eur: Decimal = Decimal(str(_HUNT["max_buy_eur"]))
    min_buy_eur: Decimal = Decimal(str(_HUNT["min_buy_eur"]))
    max_price_vs_typical: Decimal = Decimal(str(_HUNT["max_price_vs_typical"]))
    alert_price_vs_typical: Decimal = Decimal(str(_HUNT["alert_price_vs_typical"]))
    comps_price_multiple: Decimal = COMPS_PRICE_MULTIPLE
    live_search_seconds: float = LIVE_SEARCH_SECONDS
    http_timeout_seconds: float = HTTP_TIMEOUT_SECONDS

    # Conservative resale model. These are deliberately pessimistic because a false
    # positive is more expensive than missing a marginal deal.
    resale_fee_rate: Decimal = RESALE_FEE_RATE
    seller_risk_reserve_rate: Decimal = SELLER_RISK_RESERVE_RATE
    no_box_haircut_eur: Decimal = NO_BOX_HAIRCUT_EUR
    battery_under_80_haircut_rate: Decimal = BATTERY_UNDER_80_HAIRCUT_RATE
    battery_80_84_haircut_rate: Decimal = BATTERY_80_84_HAIRCUT_RATE
    battery_85_89_haircut_rate: Decimal = BATTERY_85_89_HAIRCUT_RATE

    ebay_fee_rate: Decimal = Decimal(str(_FEES["rates"]["ebay"]))
    aukro_fee_rate: Decimal = Decimal(str(_FEES["rates"]["aukro"]))
    bazos_fee_rate: Decimal = Decimal(str(_FEES["rates"]["bazos"]))
    vinted_fee_rate: Decimal = Decimal(str(_FEES["rates"]["vinted"]))

    bazos_user_agent: str = str(_HUNT["user_agent"])
    bazos_request_gap_seconds: float = float(_HUNT["request_gap_seconds"])

    github_token: str = ""
    github_repository: str = ""
    github_alert_issue: int = int(_GITHUB["alert_issue"])
    github_sell_alert_issue: int = int(_GITHUB["sell_alert_issue"])
    github_assignee: str = str(_GITHUB["assignee"])
    hunt_notify_progress: bool = bool(_GITHUB.get("notify_progress", False))
    hunt_digest_timezone: str = str(_GITHUB.get("digest_timezone", "Europe/Bratislava"))
    keepa_api_key: str = ""
    comps_db: str = COMPS_DB
    comps_ttl_days: int = COMPS_TTL_DAYS
    min_sold_sample: int = Field(default=MIN_SOLD_SAMPLE, ge=1, le=MIN_SOLD_SAMPLE_CAP)
    p25_factor: Decimal = Field(default=P25_FACTOR, gt=0, le=1)
    comps_live_queries: int = Field(default=MAX_SOLD_LOOKUPS, ge=0, le=MAX_SOLD_LOOKUPS)
    hunt_batch_db: str = HUNT_BATCH_DB
    hunt_batch_url: str = ""
    hunt_batch_token: str = Field(default="", repr=False)
    hunt_batch_page_size: int = Field(
        default=BATCH_PAGE_SIZE,
        ge=1,
        le=MAX_SOLD_LOOKUPS,
    )
    # None keeps the catalog rule (and the wider local research pass). The
    # scheduled workflow sets an explicit wall-clock-safe network-work cap.
    max_score_listings: int | None = Field(default=None, ge=1, le=MAX_SCORE_LISTINGS_CAP)
    # None = no wall-clock cap (local CLI). GitHub Actions sets 5400 so
    # scoring stops with time left to post --notify before the 110-minute
    # job is killed. Oversized env values are clamped so Settings() cannot
    # crash the hunt (that is what red-X'd Hunt alerts after PR #59).
    hunt_score_seconds: int | None = Field(default=None, ge=1, le=MAX_SCORE_SECONDS)

    @field_validator("eur_czk", "eur_pln", mode="before")
    @classmethod
    def optional_fx_rate(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("max_score_listings", mode="before")
    @classmethod
    def optional_positive_int(cls, value: object) -> object:
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("hunt_score_seconds", mode="before")
    @classmethod
    def clamp_hunt_score_seconds(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        seconds = int(value)
        if seconds < 1:
            return 1
        return min(seconds, MAX_SCORE_SECONDS)

    @field_validator("ebay_client_id", "ebay_client_secret", mode="before")
    @classmethod
    def strip_ebay_secret(cls, value: object) -> object:
        """GitHub secrets and .env pastes often carry a newline or wrapping quotes."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        return text

    @field_validator("github_alert_issue", "github_sell_alert_issue", mode="before")
    @classmethod
    def empty_issue(cls, value: object) -> object:
        if value in ("", None):
            return 0
        return value
