from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

from bazar_deals.rules import rules


def _str_enum(name: str, values: list[str]) -> type[StrEnum]:
    return StrEnum(name, {item.upper(): item for item in values})


_DOMAIN = rules()["domain"]
_HUNT = rules()["hunt"]

Marketplace = _str_enum("Marketplace", _DOMAIN["marketplaces"])
Vertical = _str_enum("Vertical", _DOMAIN["verticals"])
Condition = _str_enum("Condition", _DOMAIN["conditions"])
Action = _str_enum("Action", _DOMAIN["actions"])
ItemKind = _str_enum("ItemKind", _DOMAIN["item_kinds"])


class Money(BaseModel):
    amount: Decimal
    currency: str = _DOMAIN["default_currency"]

    def to_eur(self, eur_czk: Decimal, eur_usd: Decimal | None = None) -> Decimal:
        usd = eur_usd if eur_usd is not None else Decimal(str(_HUNT["eur_usd"]))
        code = self.currency.upper()
        if code == "EUR":
            return self.amount
        if code == "CZK":
            return (self.amount / eur_czk).quantize(Decimal("0.01"))
        if code in {"USD", "GBP"}:
            return (self.amount / usd).quantize(Decimal("0.01"))
        return self.amount


class Listing(BaseModel):
    marketplace: Marketplace
    external_id: str
    title: str
    description: str = ""
    url: HttpUrl
    price: Money
    condition: Condition = Condition.UNKNOWN
    seller_id: str | None = None
    seller_score: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime | None = None
    ends_at: datetime | None = None
    bid_count: int | None = None
    buy_now: bool = True
    search_query: str = ""
    location: str | None = None
    affiliate_url: HttpUrl | None = None
    ships_to_slovakia: bool | None = None
    shipping_cost: Money | None = None
    raw: dict = Field(default_factory=dict)

    def is_immediate_buy(self) -> bool:
        if not self.buy_now:
            return False
        if self.bid_count:
            return False
        return True


class IdentifiedItem(BaseModel):
    listing: Listing
    vertical: Vertical | None
    canonical_name: str
    brand: str | None = None
    model: str | None = None
    search_query: str = ""
    asking_sample: int = 0
    kind: str = "generic"
    sold_label: str = ""
    confidence: float = Field(ge=0, le=1)


class AIReview(BaseModel):
    approved: bool
    complete_product: bool
    canonical_name: str
    kind: str = "generic"
    quick_sale_price_eur: Decimal | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    reason: str = ""
    source_urls: list[str] = Field(default_factory=list)
    model: str = ""
    cached: bool = False


class CostBreakdown(BaseModel):
    buy_price: Decimal
    estimated_resale: Decimal
    shipping: Decimal
    fees: Decimal
    condition_haircut: Decimal
    seller_risk: Decimal
    net_profit: Decimal
    currency: str = _DOMAIN["default_currency"]


class Deal(BaseModel):
    item: IdentifiedItem
    costs: CostBreakdown
    action: Action
    reason: str
    ai_review: AIReview | None = None
