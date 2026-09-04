from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bazar_deals.rules import rules

_HUNT = rules()["hunt"]
_FEES = rules()["fees"]
_GITHUB = rules()["github"]
_EBAY = rules()["ebay"]


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
    ai_provider: str = "auto"  # auto | copilot | openai
    # Copilot Free/Student allow auto selection only. Paid seats may override
    # this with a specific model through COPILOT_MODEL.
    copilot_model: str = "auto"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    ai_review_enabled: bool = False
    ai_review_required: bool = False
    ai_max_reviews: int = 8
    # Copilot Free has a request budget, so AI identification is capped too.
    ai_max_identifications: int = 12
    ai_review_ttl_days: int = 14
    ai_min_confidence: float = 0.75
    ai_timeout_seconds: float = 90.0

    telegram_bot_token: str = ""
    telegram_chat_retro: str = ""
    telegram_chat_mineral: str = ""
    telegram_chat_apple: str = ""
    telegram_chat_network: str = ""

    eur_czk: Decimal | None = Field(default=None, gt=0)
    fx_cache: str = ".cache/ecb-fx.json"
    fx_max_age_days: int = Field(default=7, ge=0, le=30)
    fx_fee_rate: Decimal = Field(default=Decimal("0.02"), ge=0, lt=1)
    min_net_profit_eur: Decimal = Decimal("20")
    min_margin: Decimal = Decimal(str(_HUNT["min_margin"]))
    default_shipping_eur: Decimal = Decimal(str(_HUNT["default_shipping_eur"]))
    max_shipping_eur: Decimal = Decimal(str(_HUNT["max_shipping_eur"]))
    cheap_buy_eur: Decimal = Decimal(str(_HUNT["cheap_buy_eur"]))
    max_shipping_cheap_eur: Decimal = Decimal(str(_HUNT["max_shipping_cheap_eur"]))
    max_buy_eur: Decimal = Decimal(str(_HUNT["max_buy_eur"]))
    min_buy_eur: Decimal = Decimal(str(_HUNT.get("min_buy_eur", "20")))
    max_price_vs_typical: Decimal = Decimal(str(_HUNT["max_price_vs_typical"]))
    alert_price_vs_typical: Decimal = Decimal(str(_HUNT.get("alert_price_vs_typical", "1.0")))

    # Conservative resale model. These are deliberately pessimistic because a false
    # positive is more expensive than missing a marginal deal.
    resale_fee_rate: Decimal = Decimal("0.10")
    seller_risk_reserve_rate: Decimal = Decimal("0.05")
    no_box_haircut_eur: Decimal = Decimal("5")
    battery_under_80_haircut_rate: Decimal = Decimal("0.15")
    battery_80_84_haircut_rate: Decimal = Decimal("0.08")
    battery_85_89_haircut_rate: Decimal = Decimal("0.04")

    ebay_fee_rate: Decimal = Decimal(str(_FEES["rates"]["ebay"]))
    aukro_fee_rate: Decimal = Decimal(str(_FEES["rates"]["aukro"]))
    bazos_fee_rate: Decimal = Decimal(str(_FEES["rates"]["bazos"]))
    vinted_fee_rate: Decimal = Decimal(str(_FEES["rates"]["vinted"]))

    bazos_user_agent: str = str(_HUNT["user_agent"])
    bazos_request_gap_seconds: float = float(_HUNT["request_gap_seconds"])

    github_token: str = ""
    github_repository: str = ""
    github_alert_issue: int = int(_GITHUB["alert_issue"])
    github_sell_alert_issue: int = int(_GITHUB.get("sell_alert_issue", 0))
    github_assignee: str = str(_GITHUB["assignee"])
    keepa_api_key: str = ""
    comps_db: str = ".cache/bazar-comps-v2.sqlite"
    comps_ttl_days: int = int(_HUNT.get("comps_ttl_days", 7))
    comps_live_queries: int = Field(default=int(_HUNT.get("max_sold_lookups", 80)), ge=0, le=80)
    # None keeps the catalog rule (and the wider local research pass). The
    # scheduled workflow sets an explicit wall-clock-safe network-work cap.
    max_score_listings: int | None = Field(default=None, ge=1, le=200)
    # None = no wall-clock cap (local CLI). GitHub Actions sets this so the
    # scoring loop stops with time left to post --notify before the 110-minute
    # job is killed. Cap matches the two-hour hunt cadence (hunt.yml uses 5400).
    hunt_score_seconds: int | None = Field(default=None, ge=1, le=7200)

    @field_validator("eur_czk", "eur_pln", mode="before")
    @classmethod
    def optional_fx_rate(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("max_score_listings", "hunt_score_seconds", mode="before")
    @classmethod
    def optional_positive_int(cls, value: object) -> object:
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

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
