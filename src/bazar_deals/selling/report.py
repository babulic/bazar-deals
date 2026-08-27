from __future__ import annotations

import json
from decimal import Decimal

from bazar_deals.selling.channels import channel, channels, reach_matrix
from bazar_deals.selling.plan import ItemPlan, SellPlan


def _eur(value: Decimal) -> str:
    return f"{value:.2f} EUR"


def _summary(plan: SellPlan) -> list[str]:
    lines = ["# Sell plan", ""]
    lines.append(f"Inventory snapshot: {plan.collected or 'unknown'}")
    if plan.partial:
        lines.append(
            f"Partial accounts (only the visible listings were collected): "
            f"{', '.join(sorted(plan.partial))}"
        )
    coverage = ", ".join(f"{name} {count}" for name, count in plan.coverage.items())
    lines.append(f"Items in the snapshot: {len(plan.items)} ({coverage})")
    lines.append(f"Target countries: {', '.join(plan.target_countries)}")
    if plan.uncovered_countries:
        lines.append(
            f"No live channel reaches: {', '.join(plan.uncovered_countries)}"
        )
    overcharge = plan.total_overcharge_eur()
    if overcharge > 0:
        lines.append(
            f"Postage charged above the real Packeta cost across the eBay "
            f"listings: {_eur(overcharge)}"
        )
    return lines


def _channel_table(plan: SellPlan) -> list[str]:
    lines = ["", "## Channels", "", "| Channel | Country | Lang | Reach | Status | Missing items |", "|---|---|---|---|---|---:|"]
    gaps = plan.gaps_by_channel()
    for entry in channels():
        missing = gaps.get(entry.id, 0)
        lines.append(
            f"| {entry.id} | {entry.country} | {entry.language} | "
            f"{', '.join(entry.reach)} | {entry.status} | {missing or '-'} |"
        )
    return lines


def _reach_table() -> list[str]:
    lines = ["", "## Buyer reach by country", "", "| Country | Channels |", "|---|---|"]
    for country, ids in reach_matrix().items():
        labelled = ", ".join(f"{cid} ({channel(cid).status})" for cid in ids)
        lines.append(f"| {country} | {labelled} |")
    return lines


def _item_block(item_plan: ItemPlan) -> list[str]:
    item = item_plan.item
    lines = ["", f"### {item.title}", ""]
    listed = ", ".join(f"{name} {price}" for name, price in sorted(item.listed.items()))
    lines.append(f"- id: `{item.id}` | segment: {item.segment} | listed: {listed or 'nowhere'}")
    lines.append(f"- shipping weight: {item.shipping_weight_g()} g"
                 f"{'' if item.weight_is_known() else ' (assumed)'}")

    viable = item_plan.viable_countries()
    blocked = item_plan.blocked_countries()
    if viable:
        lines.append(f"- postage viable to: {', '.join(viable)}")
    if blocked:
        lines.append(f"- postage too heavy relative to price for: {', '.join(blocked)}")

    missing = item_plan.missing_channels()
    if missing:
        lines.append(f"- not listed on: {', '.join(entry.channel_id for entry in missing)}")

    for note in item_plan.notes:
        lines.append(f"- {note}")

    lines.extend(["", "| Channel | Lang | Title | Chars | Listed |", "|---|---|---|---:|---|"])
    for channel_plan in item_plan.channels:
        mark = "yes" if channel_plan.listed else "no"
        lines.append(
            f"| {channel_plan.channel_id} | {channel_plan.language} | "
            f"{channel_plan.title} | {channel_plan.title_length}/{channel_plan.title_limit} | {mark} |"
        )
    return lines


def format_markdown(plan: SellPlan, *, segment: str | None = None) -> str:
    lines = _summary(plan)
    lines.extend(_channel_table(plan))
    lines.extend(_reach_table())

    segments = [segment] if segment else sorted({p.item.segment for p in plan.items})
    for name in segments:
        item_plans = plan.by_segment(name)
        if not item_plans:
            continue
        lines.extend(["", f"## Segment: {name} ({len(item_plans)} items)"])
        for item_plan in item_plans:
            lines.extend(_item_block(item_plan))
    return "\n".join(lines) + "\n"


def format_json(plan: SellPlan, *, segment: str | None = None) -> str:
    payload = plan.model_dump(mode="json")
    if segment:
        payload["items"] = [
            entry for entry in payload["items"] if entry["item"]["segment"] == segment
        ]
    payload["total_overcharge_eur"] = str(plan.total_overcharge_eur())
    payload["gaps_by_channel"] = plan.gaps_by_channel()
    return json.dumps(payload, ensure_ascii=False, indent=2)
