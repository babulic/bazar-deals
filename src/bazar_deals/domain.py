from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, AwareDatetime
from typing import Literal

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

    def to_eur(self, eur_czk: Decimal | None, eur_usd: Decimal | None = None, *, eur_pln: Decimal | None = None) -> Decimal:
        usd = eur_usd if eur_usd is not None else Decimal(str(_HUNT["eur_usd"]))
        code = self.currency.upper()
        if code == "EUR":
            return self.amount
        if code == "PLN":
            if eur_pln is None or not eur_pln.is_finite() or eur_pln <= 0:
                raise ValueError("No valid EUR_PLN exchange rate")
            return (self.amount / eur_pln).quantize(Decimal("0.01"))
        if code == "CZK":
            if eur_czk is None or not eur_czk.is_finite() or eur_czk <= 0:
                raise ValueError("No valid EUR_CZK exchange rate")
            return (self.amount / eur_czk).quantize(Decimal("0.01"))
        if code in {"USD", "GBP"}:
            return (self.amount / usd).quantize(Decimal("0.01"))
        raise ValueError(f"Unsupported currency: {code}")


class PurchaseEvidence(BaseModel):
    checked_at: AwareDatetime
    method: Literal["delivery_sk", "pickup_sk"]
    evidence: str = Field(min_length=10)

    def is_fresh(self) -> bool:
        age = datetime.now(timezone.utc) - self.checked_at
        return timedelta(0) <= age <= timedelta(hours=24)


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
    manual_import: bool = False
    purchase_evidence: PurchaseEvidence | None = None

    def manual_purchase_verified(self) -> bool:
        return bool(self.purchase_evidence and self.purchase_evidence.is_fresh()
                    and self.shipping_cost is not None and self.buy_now)

    def purchase_allowed(self, *, require_confirmation: bool = False) -> bool:
        if self.manual_import:
            return self.manual_purchase_verified()
        return (self.ships_to_slovakia is not False and
                (not require_confirmation or self.ships_to_slovakia is True))

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
    # Price-critical facts mined from the whole ad, not only the title.
    # Typed loosely to keep bazar_deals.identity free to import this module.
    specs: object | None = None
    identified_by: str = "rules"
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
    fx_fee_reserve: Decimal = Decimal("0")  # Included in fees, not an additional subtraction.


class Deal(BaseModel):
    item: IdentifiedItem
    costs: CostBreakdown
    action: Action
    reason: str
    ai_review: AIReview | None = None
