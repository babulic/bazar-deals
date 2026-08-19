from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pydantic import BaseModel, Field, HttpUrl


class Marketplace(StrEnum):
    EBAY = "ebay"
    AUKRO = "aukro"
    BAZOS = "bazos"
    VINTED = "vinted"


class Vertical(StrEnum):
    RETRO = "retro"
    MINERAL = "mineral"
    APPLE = "apple"
    NETWORK = "network"


class Condition(StrEnum):
    NEW = "new"
    LIKE_NEW = "like_new"
    USED = "used"
    FOR_PARTS = "for_parts"
    UNKNOWN = "unknown"


class Action(StrEnum):
    BUY = "buy"
    ALERT = "alert"
    SKIP = "skip"


class Money(BaseModel):
    amount: Decimal
    currency: str = "EUR"

    def to_eur(self, eur_czk: Decimal, eur_usd: Decimal = Decimal("1.08")) -> Decimal:
        code = self.currency.upper()
        if code == "EUR":
            return self.amount
        if code == "CZK":
            return (self.amount / eur_czk).quantize(Decimal("0.01"))
        if code in {"USD", "GBP"}:
            return (self.amount / eur_usd).quantize(Decimal("0.01"))
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
    location: str | None = None
    affiliate_url: HttpUrl | None = None
    raw: dict = Field(default_factory=dict)

    def is_immediate_buy(self) -> bool:
        """True when the listed price can be paid now, not an auction start/current bid."""
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
    confidence: float = Field(ge=0, le=1)


class CostBreakdown(BaseModel):
    buy_price: Decimal
    estimated_resale: Decimal
    shipping: Decimal
    fees: Decimal
    condition_haircut: Decimal
    seller_risk: Decimal
    net_profit: Decimal
    currency: str = "EUR"


class Deal(BaseModel):
    item: IdentifiedItem
    costs: CostBreakdown
    action: Action
    reason: str
