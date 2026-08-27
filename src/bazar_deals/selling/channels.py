from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import BaseModel

from bazar_deals.rules import rules

# A channel that is neither live nor ruled out is an opportunity, so the two
# actionable states are kept apart from the ones already decided.
ACTIVE = "active"
MISSING = "missing"
REJECTED = "rejected"


class Channel(BaseModel):
    id: str
    marketplace: str
    # Key under which an item's price appears in the inventory snapshot. Only the
    # four channels with a real account have one; for the rest it stays the
    # channel id, which never matches, so the item reads as not listed there.
    listed_as: str = ""
    country: str
    language: str
    title_limit: int
    fee_rate: Decimal
    reach: list[str]
    segments: list[str]
    status: str
    note: str = ""

    def inventory_key(self) -> str:
        return self.listed_as or self.id

    def is_open(self) -> bool:
        return self.status != REJECTED

    def serves(self, segment: str) -> bool:
        return segment in self.segments

    def reaches(self, country: str) -> bool:
        return country.upper() in {code.upper() for code in self.reach}


@lru_cache(maxsize=1)
def channels() -> tuple[Channel, ...]:
    return tuple(Channel(**entry) for entry in rules()["selling"]["channels"])


def channel(channel_id: str) -> Channel:
    for entry in channels():
        if entry.id == channel_id:
            return entry
    raise KeyError(f"Unknown channel {channel_id!r}")


def channels_for_segment(segment: str, *, include_rejected: bool = False) -> list[Channel]:
    return [
        entry
        for entry in channels()
        if entry.serves(segment) and (include_rejected or entry.is_open())
    ]


def active_marketplaces() -> set[str]:
    return {entry.marketplace for entry in channels() if entry.status == ACTIVE}


def reach_matrix() -> dict[str, list[str]]:
    """Buyer country -> channel ids that expose stock to it, live ones first."""
    matrix: dict[str, list[str]] = {}
    for entry in channels():
        if not entry.is_open():
            continue
        for country in entry.reach:
            matrix.setdefault(country.upper(), []).append(entry.id)
    order = {ACTIVE: 0, MISSING: 1}
    for country, ids in matrix.items():
        matrix[country] = sorted(ids, key=lambda cid: (order.get(channel(cid).status, 2), cid))
    return dict(sorted(matrix.items()))


def uncovered_countries(target_countries: list[str]) -> list[str]:
    """Target countries no currently live channel reaches."""
    live = {
        country.upper()
        for entry in channels()
        if entry.status == ACTIVE
        for country in entry.reach
    }
    return [code for code in target_countries if code.upper() not in live]
