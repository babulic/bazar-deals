from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from bazar_deals.rules import rules

_CENT = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class ShippingQuote(BaseModel):
    country: str
    mode: str
    weight_g: int
    base_eur: Decimal
    fuel_eur: Decimal
    toll_eur: Decimal
    total_eur: Decimal

    def label(self) -> str:
        return f"{self.country} {self.mode} {self.total_eur} EUR"


class PacketaRates:
    """Packeta price list for the countries a Slovak business account can reach.

    The shipped numbers are public list prices excluding VAT. A business contract
    is negotiated individually and is always cheaper, so override the
    `selling.packeta.destinations` block with the real rates before treating any
    margin as final.
    """

    def __init__(self, config: dict | None = None) -> None:
        data = config if config is not None else rules()["selling"]["packeta"]
        self._destinations: dict[str, dict] = data["destinations"]
        self.fuel_surcharge_rate = Decimal(str(data["fuel_surcharge_rate"]))
        self.toll_eur_per_kg = Decimal(str(data["toll_eur_per_kg"]))
        self.max_weight_kg = int(data["max_weight_kg"])

    def countries(self) -> list[str]:
        return sorted(self._destinations)

    def schengen_countries(self) -> list[str]:
        return sorted(k for k, v in self._destinations.items() if v.get("schengen"))

    def serves(self, country: str) -> bool:
        return country.upper() in self._destinations

    def quote(self, country: str, *, weight_g: int = 500, mode: str = "pickup") -> ShippingQuote:
        code = country.upper()
        destination = self._destinations.get(code)
        if destination is None:
            raise KeyError(f"Packeta does not serve {code} in the configured rate card")
        if mode not in {"pickup", "home"}:
            raise ValueError(f"Unknown delivery mode {mode!r}")
        if weight_g > self.max_weight_kg * 1000:
            raise ValueError(f"{weight_g} g exceeds the {self.max_weight_kg} kg Packeta limit")

        base = Decimal(str(destination[mode]))
        # The toll surcharge is charged per started kilogram, so a 103 g specimen
        # and a 900 g power supply cost the same.
        started_kg = max(1, math.ceil(weight_g / 1000))
        toll = self.toll_eur_per_kg * started_kg
        fuel = base * self.fuel_surcharge_rate
        return ShippingQuote(
            country=code,
            mode=mode,
            weight_g=weight_g,
            base_eur=_round(base),
            fuel_eur=_round(fuel),
            toll_eur=_round(toll),
            total_eur=_round(base + fuel + toll),
        )

    def cheapest(self, countries: list[str], *, weight_g: int = 500) -> ShippingQuote | None:
        quotes = [
            self.quote(country, weight_g=weight_g)
            for country in countries
            if self.serves(country)
        ]
        return min(quotes, key=lambda quote: quote.total_eur) if quotes else None
