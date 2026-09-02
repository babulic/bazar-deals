from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from bazar_deals.rules import rules

PACKAGED_INVENTORY = Path(__file__).resolve().parent.parent / "data" / "inventory.yaml"
# `sell --refresh` writes here so a live run never rewrites the annotated seed.
REFRESHED_INVENTORY = Path(".cache/sell-inventory.yaml")

# Used when a listing never stated a weight. Everything in this inventory is a
# small specimen or a chip, and every Packeta rate card starts at 1 kg anyway.
DEFAULT_WEIGHT_G = 500


class InventoryItem(BaseModel):
    id: str
    segment: str
    title: str
    species: list[str] = Field(default_factory=list)
    form: str = ""
    # Stated colour of the specimen or finished piece, when the listing names one.
    color: str = ""
    part_numbers: list[str] = Field(default_factory=list)
    # Low-value terms worth adding only where the channel has spare characters.
    keywords: list[str] = Field(default_factory=list)
    # Distinguishing substrings for variants whose titles are otherwise
    # identical, such as the same chip from two production years.
    match_hints: list[str] = Field(default_factory=list)
    origin: str = ""
    locality: str = ""
    weight_g: int | None = None
    listed: dict[str, Decimal] = Field(default_factory=dict)
    # Watchlist counts per marketplace: buyers who have already raised a hand.
    watchers: dict[str, int] = Field(default_factory=dict)
    views: dict[str, int] = Field(default_factory=dict)
    ship_eur: Decimal | None = None
    # Public listing photos from collect/refresh, used to confirm a want-ad
    # is the same object and not a similarly titled phone or other watch.
    image_urls: list[str] = Field(default_factory=list)

    def total_watchers(self) -> int:
        return sum(self.watchers.values())

    def total_views(self) -> int:
        return sum(self.views.values())

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
    source = path
    if source is None:
        source = REFRESHED_INVENTORY if REFRESHED_INVENTORY.is_file() else PACKAGED_INVENTORY
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    meta = data.get("meta") or {}
    return Inventory(
        collected=str(meta.get("collected", "")),
        partial=list(meta.get("partial") or []),
        counts=dict(meta.get("counts") or {}),
        items=[InventoryItem(**entry) for entry in data.get("items") or []],
    )


def save_inventory(inventory: Inventory, path: Path | None = None) -> Path:
    target = path or REFRESHED_INVENTORY
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "collected": inventory.collected,
            "counts": inventory.counts,
            "partial": inventory.partial,
        },
        "items": [
            item.model_dump(mode="json", exclude_defaults=True) for item in inventory.items
        ],
    }
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def known_segments() -> list[str]:
    return sorted(rules()["selling"]["segments"])
