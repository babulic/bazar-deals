"""Local, user-selected offers. Never fetch URLs or reuse browser credentials."""
from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime

from bazar_deals.adapters.central_europe import SITES, _safe_url, _exclude_demands
from bazar_deals.domain import Listing, Money, Marketplace, PurchaseEvidence


class ManualOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    marketplace: str
    external_id: str = Field(min_length=1)
    kind: Literal["offer", "wanted"] = "offer"
    title: str = Field(min_length=3)
    description: str = Field(min_length=10)
    url: str
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Literal["EUR", "CZK", "PLN"]
    available: bool
    checked_at: AwareDatetime
    fulfillment: Literal["unknown", "delivery_sk", "pickup_sk"] = "unknown"
    fulfillment_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    fulfillment_currency: Literal["EUR", "CZK", "PLN"] = "EUR"
    evidence: str = ""

    def listing(self) -> Listing:
        if self.marketplace not in SITES or not _safe_url(self.url, self.marketplace):
            raise ValueError("URL must be HTTPS on the selected marketplace's own domain")
        proof = None
        if self.fulfillment != "unknown":
            proof = PurchaseEvidence(checked_at=self.checked_at, method=self.fulfillment,
                                     evidence=self.evidence)
        shipping = None if self.fulfillment_cost is None else Money(
            amount=self.fulfillment_cost, currency=self.fulfillment_currency)
        return Listing(marketplace=Marketplace(self.marketplace), external_id=self.external_id,
                       title=self.title, description=self.description, url=self.url,
                       price=Money(amount=self.price, currency=self.currency),
                       buy_now=self.available and self.kind == "offer",
                       ships_to_slovakia=True if proof and proof.method == "delivery_sk" else None,
                       shipping_cost=shipping, manual_import=True, purchase_evidence=proof,
                       raw={"manual_kind": self.kind, "available": self.available,
                            "checked_at": self.checked_at.isoformat()})


def load_manual_offers(path: Path) -> list[Listing]:
    """All-or-nothing validation: one malformed row aborts the import."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle)) if path.suffix.lower() == ".csv" else json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("Manual import must contain a list of offers")
    listings = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        try:
            row = {key: value for key, value in row.items() if value != ""}
            listing = ManualOffer.model_validate(row).listing()
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid manual import row {index}: {exc}") from exc
        key = (listing.marketplace, listing.external_id)
        if key in seen:
            raise ValueError(f"Duplicate manual import row {index}")
        seen.add(key)
        listings.append(listing)
    return _exclude_demands(listings)
