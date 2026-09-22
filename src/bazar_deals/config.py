from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bazar_deals.rules import rules

_HUNT = rules()["hunt"]
_FEES = rules()["fees"]
_GITHUB = rules()["github"]
_EBAY = rules()["ebay"]
_AI = rules()["ai"]


def _dec(mapping: dict, key: str) -> Decimal:
    return Decimal(str(mapping[key]))


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
    ai_provider: str = str(_AI["provider"])  # auto | copilot | openai
    # Copilot Free/Student allow auto selection only. Paid seats may override
    # this with a specific model through COPILOT_MODEL.
    copilot_model: str = str(_AI["copilot_model"])
    openai_api_key: str = ""
    openai_base_url: str = str(_AI["openai_base_url"])
    openai_model: str = str(_AI["openai_model"])
    ai_review_enabled: bool = False
    ai_review_required: bool = False
    ai_max_reviews: int = int(_AI["max_reviews"])
    # Copilot Free has a request budget, so AI identification is capped too.
    ai_max_identifications: int = int(_AI["max_identifications"])
    ai_review_ttl_days: int = int(_AI["review_ttl_days"])
    ai_min_confidence: float = float(_AI["min_confidence"])
    ai_timeout_seconds: float = float(_AI["timeout_seconds"])

    telegram_bot_token: str = ""
    telegram_chat_retro: str = ""
    telegram_chat_mineral: str = ""
    telegram_chat_apple: str = ""
    telegram_chat_network: str = ""

    eur_czk: Decimal | None = Field(default=None, gt=0)
    fx_cache: str = str(_HUNT["fx_cache"])
    fx_max_age_days: int = Field(
        default=int(_HUNT["fx_max_age_days"]),
        ge=0,
        le=int(_HUNT["fx_max_age_days_cap"]),
    )
    fx_fee_rate: Decimal = Field(default=_dec(_FEES, "fx_fee_rate"), ge=0, lt=1)
    min_net_profit_eur: Decimal = _dec(_HUNT, "min_net_profit_eur")
    alert_min_net_profit_eur: Decimal = _dec(_HUNT, "alert_min_net_profit_eur")
    min_margin: Decimal = _dec(_HUNT, "min_margin")
    default_shipping_eur: Decimal = _dec(_HUNT, "default_shipping_eur")
    max_shipping_eur: Decimal = _dec(_HUNT, "max_shipping_eur")
    cheap_buy_eur: Decimal = _dec(_HUNT, "cheap_buy_eur")
    max_shipping_cheap_eur: Decimal = _dec(_HUNT, "max_shipping_cheap_eur")
    max_buy_eur: Decimal = _dec(_HUNT, "max_buy_eur")
    min_buy_eur: Decimal = _dec(_HUNT, "min_buy_eur")
    max_price_vs_typical: Decimal = _dec(_HUNT, "max_price_vs_typical")
    alert_price_vs_typical: Decimal = _dec(_HUNT, "alert_price_vs_typical")
    comps_price_multiple: Decimal = _dec(_HUNT, "comps_price_multiple")
    live_search_seconds: float = float(_HUNT["live_search_seconds"])
    http_timeout_seconds: float = float(_HUNT["http_timeout_seconds"])

    # Conservative resale model. These are deliberately pessimistic because a false
    # positive is more expensive than missing a marginal deal.
    resale_fee_rate: Decimal = _dec(_FEES, "resale_fee_rate")
    seller_risk_reserve_rate: Decimal = _dec(_FEES, "seller_risk_reserve_rate")
    no_box_haircut_eur: Decimal = _dec(_HUNT, "no_box_haircut_eur")
    min_battery_health_percent: int = int(_HUNT["min_battery_health_percent"])
    battery_under_pct: int = int(_HUNT["battery_under_pct"])
    battery_mid_pct: int = int(_HUNT["battery_mid_pct"])
    battery_good_pct: int = int(_HUNT["battery_good_pct"])
    battery_under_80_haircut_rate: Decimal = _dec(_HUNT, "battery_under_80_haircut_rate")
    battery_80_84_haircut_rate: Decimal = _dec(_HUNT, "battery_80_84_haircut_rate")
    battery_85_89_haircut_rate: Decimal = _dec(_HUNT, "battery_85_89_haircut_rate")

    ebay_fee_rate: Decimal = _dec(_FEES["rates"], "ebay")
    aukro_fee_rate: Decimal = _dec(_FEES["rates"], "aukro")
    bazos_fee_rate: Decimal = _dec(_FEES["rates"], "bazos")
    vinted_fee_rate: Decimal = _dec(_FEES["rates"], "vinted")

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
    comps_db: str = str(_HUNT["comps_db"])
    comps_ttl_days: int = int(_HUNT["comps_ttl_days"])
    min_sold_sample: int = Field(
        default=int(_HUNT["min_sold_sample"]),
        ge=1,
        le=int(_HUNT["min_sold_sample_cap"]),
    )
    p25_factor: Decimal = Field(default=_dec(_HUNT, "p25_factor"), gt=0, le=1)
    comps_live_queries: int = Field(
        default=int(_HUNT["max_sold_lookups"]),
        ge=0,
        le=int(_HUNT["max_sold_lookups"]),
    )
    hunt_batch_db: str = str(_HUNT["hunt_batch_db"])
    hunt_batch_url: str = ""
    hunt_batch_token: str = Field(default="", repr=False)
    hunt_batch_page_size: int = Field(
        default=int(_HUNT["batch_page_size"]),
        ge=1,
        le=int(_HUNT["max_sold_lookups"]),
    )
    # None keeps the catalog rule (and the wider local research pass). The
    # scheduled workflow sets an explicit wall-clock-safe network-work cap.
    max_score_listings: int | None = Field(
        default=None,
        ge=1,
        le=int(_HUNT["max_score_listings_cap"]),
    )
    # None = no wall-clock cap (local CLI). GitHub Actions leaves this empty and
    # bounds work with persisted pages. Oversized env values are clamped so
    # Settings() cannot crash the hunt (that is what red-X'd Hunt alerts after PR #59).
    hunt_score_seconds: int | None = Field(
        default=None,
        ge=1,
        le=int(_HUNT["max_score_seconds"]),
    )

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
        return min(seconds, int(_HUNT["max_score_seconds"]))

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
