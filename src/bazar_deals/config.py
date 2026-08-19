from decimal import Decimal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bazar_deals.rules import rules

_HUNT = rules()["hunt"]
_FEES = rules()["fees"]
_GITHUB = rules()["github"]
_EBAY = rules()["ebay"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = str(_EBAY["marketplace_id"])
    ebay_campaign_id: str = ""

    aukro_api_token: str = ""

    vinted_access_key: str = ""
    vinted_signing_key: str = ""

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    telegram_bot_token: str = ""
    telegram_chat_retro: str = ""
    telegram_chat_mineral: str = ""
    telegram_chat_apple: str = ""
    telegram_chat_network: str = ""

    eur_czk: Decimal = Decimal(str(_HUNT["eur_czk"]))
    min_net_profit_eur: Decimal = Decimal(str(_HUNT["min_net_profit_eur"]))
    min_margin: Decimal = Decimal(str(_HUNT["min_margin"]))
    default_shipping_eur: Decimal = Decimal(str(_HUNT["default_shipping_eur"]))
    max_shipping_eur: Decimal = Decimal(str(_HUNT["max_shipping_eur"]))
    cheap_buy_eur: Decimal = Decimal(str(_HUNT["cheap_buy_eur"]))
    max_shipping_cheap_eur: Decimal = Decimal(str(_HUNT["max_shipping_cheap_eur"]))
    max_buy_eur: Decimal = Decimal(str(_HUNT["max_buy_eur"]))
    min_buy_eur: Decimal = Decimal(str(_HUNT.get("min_buy_eur", "10")))
    max_price_vs_typical: Decimal = Decimal(str(_HUNT["max_price_vs_typical"]))
    alert_price_vs_typical: Decimal = Decimal(str(_HUNT.get("alert_price_vs_typical", "1.0")))
    ebay_fee_rate: Decimal = Decimal(str(_FEES["rates"]["ebay"]))
    aukro_fee_rate: Decimal = Decimal(str(_FEES["rates"]["aukro"]))
    bazos_fee_rate: Decimal = Decimal(str(_FEES["rates"]["bazos"]))
    vinted_fee_rate: Decimal = Decimal(str(_FEES["rates"]["vinted"]))

    bazos_user_agent: str = str(_HUNT["user_agent"])
    bazos_request_gap_seconds: float = float(_HUNT["request_gap_seconds"])

    github_token: str = ""
    github_repository: str = ""
    github_alert_issue: int = int(_GITHUB["alert_issue"])
    github_assignee: str = str(_GITHUB["assignee"])
    keepa_api_key: str = ""
    comps_db: str = str(_HUNT.get("comps_db", ".cache/bazar-comps.sqlite"))
    comps_ttl_days: int = int(_HUNT.get("comps_ttl_days", 7))

    @field_validator("github_alert_issue", mode="before")
    @classmethod
    def empty_issue(cls, value: object) -> object:
        if value in ("", None):
            return 0
        return value
