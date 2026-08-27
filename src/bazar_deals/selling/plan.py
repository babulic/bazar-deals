from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from bazar_deals.rules import rules
from bazar_deals.selling.channels import (
    ACTIVE,
    Channel,
    channels_for_segment,
    uncovered_countries,
)
from bazar_deals.selling.inventory import Inventory, InventoryItem, load_inventory
from bazar_deals.selling.packeta import PacketaRates, ShippingQuote
from bazar_deals.selling.titles import build_title, localize_locality

_CENT = Decimal("0.01")

# Only worth the curation effort and the higher commission above this price.
CATAWIKI_FLOOR_EUR = Decimal("75")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class ShippingOption(BaseModel):
    quote: ShippingQuote
    ratio: Decimal
    viable: bool


class ChannelPlan(BaseModel):
    channel_id: str
    marketplace: str
    country: str
    language: str
    status: str
    listed: bool
    title: str
    title_limit: int
    fee_rate: Decimal
    price_eur: Decimal
    net_after_fee_eur: Decimal

    @property
    def title_length(self) -> int:
        return len(self.title)


class ItemPlan(BaseModel):
    item: InventoryItem
    channels: list[ChannelPlan] = Field(default_factory=list)
    shipping: list[ShippingOption] = Field(default_factory=list)
    overcharge_eur: Decimal | None = None
    notes: list[str] = Field(default_factory=list)

    def missing_channels(self) -> list[ChannelPlan]:
        return [plan for plan in self.channels if not plan.listed]

    def live_channels(self) -> list[ChannelPlan]:
        return [plan for plan in self.channels if plan.listed]

    def viable_countries(self) -> list[str]:
        return [option.quote.country for option in self.shipping if option.viable]

    def blocked_countries(self) -> list[str]:
        return [option.quote.country for option in self.shipping if not option.viable]


class SellPlan(BaseModel):
    collected: str = ""
    partial: list[str] = Field(default_factory=list)
    target_countries: list[str] = Field(default_factory=list)
    uncovered_countries: list[str] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    items: list[ItemPlan] = Field(default_factory=list)

    def total_overcharge_eur(self) -> Decimal:
        return _round(
            sum(
                (plan.overcharge_eur for plan in self.items if plan.overcharge_eur),
                Decimal("0"),
            )
        )

    def gaps_by_channel(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for plan in self.items:
            for missing in plan.missing_channels():
                counts[missing.channel_id] = counts.get(missing.channel_id, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))

    def by_segment(self, segment: str) -> list[ItemPlan]:
        return [plan for plan in self.items if plan.item.segment == segment]


def _channel_price(item: InventoryItem, channel: Channel) -> Decimal:
    """Price already asked on the channel, otherwise the best price elsewhere."""
    key = channel.inventory_key()
    if key in item.listed:
        return item.listed[key]
    return item.price()


def _shipping_options(
    item: InventoryItem,
    rates: PacketaRates,
    target_countries: list[str],
    max_ratio: Decimal,
) -> list[ShippingOption]:
    price = item.price()
    options: list[ShippingOption] = []
    for country in target_countries:
        if not rates.serves(country):
            continue
        quote = rates.quote(country, weight_g=item.shipping_weight_g())
        ratio = _round(quote.total_eur / price) if price > 0 else Decimal("0")
        options.append(
            ShippingOption(quote=quote, ratio=ratio, viable=price > 0 and ratio <= max_ratio)
        )
    return options


def _notes(
    item: InventoryItem,
    plans: list[ChannelPlan],
    options: list[ShippingOption],
    overcharge: Decimal | None,
    overcharge_flag: Decimal,
) -> list[str]:
    notes: list[str] = []

    if overcharge is not None and overcharge >= overcharge_flag:
        notes.append(
            f"eBay ships for {item.ship_eur} EUR but Packeta to Austria costs "
            f"{item.ship_eur - overcharge} EUR: {overcharge} EUR of postage is "
            "suppressing the sale."
        )

    blocked = [option for option in options if not option.viable]
    if blocked and len(blocked) == len(options):
        notes.append(
            "Postage exceeds the viable share of the price everywhere: sell at "
            "home or bundle it with a larger order."
        )

    if not item.weight_is_known():
        notes.append("No weight on the listing, so postage is quoted from the default tier.")

    live = {plan.marketplace for plan in plans if plan.listed}
    if live == {"vinted"}:
        notes.append("Listed on Vinted only, where collectors of this stock do not look.")

    if item.segment == "minerals" and item.locality:
        german, slovak = localize_locality(item.locality, "de")
        if german.lower() != slovak.lower():
            notes.append(
                f"German and Austrian collectors search the historic name "
                f"{german!r}, not {slovak!r}."
            )

    if item.segment == "minerals" and item.price() >= CATAWIKI_FLOOR_EUR:
        notes.append("Priced high enough for a curated Catawiki auction.")

    return notes


def build_plan(
    inventory: Inventory | None = None,
    *,
    rates: PacketaRates | None = None,
    config: dict | None = None,
) -> SellPlan:
    settings = config if config is not None else rules()["selling"]
    stock = inventory if inventory is not None else load_inventory()
    packeta = rates if rates is not None else PacketaRates()

    target_countries = [code.upper() for code in settings["target_countries"]]
    max_ratio = Decimal(str(settings["max_shipping_ratio"]))
    overcharge_flag = Decimal(str(settings["overcharge_flag_eur"]))

    item_plans: list[ItemPlan] = []
    for item in stock.items:
        channel_plans: list[ChannelPlan] = []
        for channel in channels_for_segment(item.segment):
            price = _channel_price(item, channel)
            channel_plans.append(
                ChannelPlan(
                    channel_id=channel.id,
                    marketplace=channel.marketplace,
                    country=channel.country,
                    language=channel.language,
                    status=channel.status,
                    listed=channel.inventory_key() in item.listed,
                    title=build_title(item, language=channel.language, limit=channel.title_limit),
                    title_limit=channel.title_limit,
                    fee_rate=channel.fee_rate,
                    price_eur=price,
                    net_after_fee_eur=_round(price * (Decimal("1") - channel.fee_rate)),
                )
            )

        options = _shipping_options(item, packeta, target_countries, max_ratio)

        overcharge: Decimal | None = None
        if item.ship_eur is not None and packeta.serves("AT"):
            real = packeta.quote("AT", weight_g=item.shipping_weight_g()).total_eur
            overcharge = _round(item.ship_eur - real)

        item_plans.append(
            ItemPlan(
                item=item,
                channels=channel_plans,
                shipping=options,
                overcharge_eur=overcharge,
                notes=_notes(item, channel_plans, options, overcharge, overcharge_flag),
            )
        )

    return SellPlan(
        collected=stock.collected,
        partial=stock.partial,
        target_countries=target_countries,
        uncovered_countries=uncovered_countries(target_countries),
        coverage=stock.coverage(),
        items=item_plans,
    )


def active_channel_ids() -> list[str]:
    seen: list[str] = []
    for segment in rules()["selling"]["segments"]:
        for channel in channels_for_segment(segment):
            if channel.status == ACTIVE and channel.id not in seen:
                seen.append(channel.id)
    return sorted(seen)
