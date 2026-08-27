from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from bazar_deals.rules import rules

_PACKAGE_INVENTORY = Path(__file__).resolve().parent.parent / "data" / "inventory.yaml"

# Used when a listing never stated a weight. Everything in this inventory is a
# small specimen or a chip, and every Packeta rate card starts at 1 kg anyway.
DEFAULT_WEIGHT_G = 500


class InventoryItem(BaseModel):
    id: str
    segment: str
    title: str
    species: list[str] = Field(default_factory=list)
    form: str = ""
    part_numbers: list[str] = Field(default_factory=list)
    # Low-value terms worth adding only where the channel has spare characters.
    keywords: list[str] = Field(default_factory=list)
    origin: str = ""
    locality: str = ""
    weight_g: int | None = None
    listed: dict[str, Decimal] = Field(default_factory=dict)
    ship_eur: Decimal | None = None

    def shipping_weight_g(self) -> int:
        return self.weight_g or DEFAULT_WEIGHT_G

    def weight_is_known(self) -> bool:
        return self.weight_g is not None

    def price(self) -> Decimal:
        """Representative asking price: the highest a buyer is already paying."""
        return max(self.listed.values()) if self.listed else Decimal("0")

    def home_price(self) -> Decimal:
        """Slovak asking price, falling back to whatever else is listed."""
        for marketplace in ("bazos", "aukro", "vinted"):
            if marketplace in self.listed:
                return self.listed[marketplace]
        return self.price()

    def marketplaces(self) -> set[str]:
        return set(self.listed)

    def missing_from(self, marketplaces: set[str]) -> list[str]:
        return sorted(marketplaces - self.marketplaces())


class Inventory(BaseModel):
    collected: str = ""
    partial: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[InventoryItem] = Field(default_factory=list)

    def by_segment(self, segment: str) -> list[InventoryItem]:
        return [item for item in self.items if item.segment == segment]

    def segments(self) -> list[str]:
        return sorted({item.segment for item in self.items})

    def get(self, item_id: str) -> InventoryItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(f"Unknown inventory item {item_id!r}")

    def coverage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            for marketplace in item.listed:
                counts[marketplace] = counts.get(marketplace, 0) + 1
        return dict(sorted(counts.items()))


def load_inventory(path: Path | None = None) -> Inventory:
    source = path or _PACKAGE_INVENTORY
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    meta = data.get("meta") or {}
    return Inventory(
        collected=str(meta.get("collected", "")),
        partial=list(meta.get("partial") or []),
        counts=dict(meta.get("counts") or {}),
        items=[InventoryItem(**entry) for entry in data.get("items") or []],
    )


def known_segments() -> list[str]:
    return sorted(rules()["selling"]["segments"])
