from decimal import Decimal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = "EBAY_DE"
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

    eur_czk: Decimal = Decimal("24.5")
    min_net_profit_eur: Decimal = Decimal("20")
    min_margin: Decimal = Decimal("0.25")
    default_shipping_eur: Decimal = Decimal("8")
    max_buy_eur: Decimal = Decimal("60")
    max_price_vs_typical: Decimal = Decimal("1.0")
    ebay_fee_rate: Decimal = Decimal("0.13")
    aukro_fee_rate: Decimal = Decimal("0.11")
    bazos_fee_rate: Decimal = Decimal("0")
    vinted_fee_rate: Decimal = Decimal("0.05")

    bazos_user_agent: str = "bazar-deals/0.1 (+https://github.com/babulic/bazar-deals)"
    bazos_request_gap_seconds: float = 2.0

    github_token: str = ""
    github_repository: str = ""
    github_alert_issue: int = 0
    github_assignee: str = ""
    keepa_api_key: str = ""

    @field_validator("github_alert_issue", mode="before")
    @classmethod
    def empty_issue(cls, value: object) -> object:
        if value in ("", None):
            return 0
        return value
