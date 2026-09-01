"""Zero-result loop: after 0 BUY or 0 sell matches, expand search toward profit."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path


def enable_hunt_research() -> None:
    os.environ["BAZAR_HUNT_RESEARCH"] = "1"
    os.environ["BAZAR_HUNT_EXPAND"] = "1"


def should_research_loop(*, buy_count: int, already_research: bool, offline: bool) -> bool:
    """In-process retry after 0 BUY. `--research` is the retry; offline stays one-shot."""
    return buy_count <= 0 and not already_research and not offline


def retryable_sell_errors(notes: list[str] | tuple[str, ...] = ()) -> list[str]:
    """429/throttle notes. Login walls after HTML+index miss are not a retry reason."""
    found: list[str] = []
    for note in notes:
        folded = (note or "").casefold()
        if "login wall" in folded:
            continue
        if "http 429" in folded or "too many requests" in folded:
            found.append(note)
    return found


def should_sell_research_loop(
    *,
    buyers: int,
    notes: list[str] | tuple[str, ...] = (),
    already_research: bool,
    offline: bool,
) -> bool:
    """In-process sell retry after 0 kupci or a throttled eBay pass."""
    if already_research or offline:
        return False
    if retryable_sell_errors(notes):
        return True
    return buyers <= 0


def write_github_output(**fields: object) -> None:
    """Append `key=value` lines for a GitHub Actions job output."""
    path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in fields.items():
            handle.write(f"{key}={value}\n")


def write_run_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def hunt_research_hint(funnel: Counter[str] | dict[str, int]) -> str:
    """What the next pass should change when the hunt found 0 BUY."""
    n = funnel.get if isinstance(funnel, dict) else funnel.get
    no_comps = int(n("no_sold_comps") or 0)
    weak = int(n("identity_weak") or 0)
    above = int(n("above_typical") or 0)
    usable = int(n("usable") or 0)
    if usable == 0:
        return "0 usable ads — widen boards and fast-moving SKUs, keep 2 kg / shoebox gates"
    if no_comps >= max(above, weak, 1):
        return (
            f"{no_comps} ads without 5 comps — expand targeted SKUs and live price-book queries"
        )
    if weak:
        return f"{weak} weak identities — prefer branded, identifiable fast-movers"
    if above:
        return f"{above} over usual price — hunt different SKUs, not cheaper-looking junk"
    return "0 BUY — expand sites and assortment toward net profit >= 20 EUR"


def sell_research_hint(*, buyers: int, fetched: int) -> str:
    if buyers:
        return ""
    if fetched == 0:
        return "0 want-ads fetched — retry more boards and stock aliases"
    return "0 kúpim matches — expand WTB phrases, Vinted hosts, and glossary aliases"
