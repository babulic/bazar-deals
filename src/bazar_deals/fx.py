"""One dated ECB snapshot for CZK and PLN; no network during Settings construction."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

import httpx

from bazar_deals.config import Settings

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CUBE = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"


def _valid_date(value: str, today: date, max_age: int) -> date:
    published = date.fromisoformat(value)
    if not 0 <= (today - published).days <= max_age:
        raise ValueError("ECB snapshot has a future or stale publication date")
    return published


def _rates(values: dict) -> dict[str, Decimal]:
    rates = {}
    for currency in ("CZK", "PLN"):
        value = Decimal(str(values[currency]))
        if not value.is_finite() or value <= 0:
            raise ValueError(f"Invalid {currency} exchange rate")
        rates[currency] = value
    return rates


def parse_ecb(body: bytes, *, today: date, max_age: int) -> tuple[date, dict[str, Decimal]]:
    if len(body) > 100_000 or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("Unexpected ECB XML document")
    root = ElementTree.fromstring(body)
    snapshots = [node for node in root.iter(_CUBE) if "time" in node.attrib]
    if len(snapshots) != 1:
        raise ValueError("Expected one dated ECB snapshot")
    snapshot = snapshots[0]
    published = _valid_date(snapshot.attrib["time"], today, max_age)
    values = {}
    for node in snapshot.findall(_CUBE):
        currency = node.attrib.get("currency")
        if currency in ("CZK", "PLN"):
            if currency in values:
                raise ValueError("Duplicate ECB currency")
            values[currency] = node.attrib.get("rate")
    return published, _rates(values)


def _read_cache(path: Path, today: date, max_age: int):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["source"] != ECB_URL:
            return None
        published = _valid_date(payload["published"], today, max_age)
        checked = date.fromisoformat(payload["checked_on"])
        if not published <= checked <= today:
            return None
        return published, _rates(payload["rates"]), checked
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation):
        return None


def _write_cache(path: Path, published: date, rates: dict[str, Decimal], today: date):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"source": ECB_URL, "published": published.isoformat(),
                       "checked_on": today.isoformat(),
                       "rates": {currency: str(rate) for currency, rate in rates.items()}}, handle)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_exchange_rates(
    settings: Settings, *, offline: bool = False,
    client: httpx.Client | None = None, today: date | None = None,
) -> tuple[Settings, list[str]]:
    """Resolve rates once at the CLI boundary; explicit settings remain overrides.

    Failed/missing/stale data leave the affected rates unset. Consumers must skip
    that currency rather than fall back to an undated hard-coded number.
    """
    today = today or datetime.now(timezone.utc).date()
    notes = [f"FX: manual EUR_{code}={getattr(settings, 'eur_' + code.lower())}"
             for code in ("CZK", "PLN") if getattr(settings, "eur_" + code.lower()) is not None]
    missing = [code for code in ("CZK", "PLN") if getattr(settings, "eur_" + code.lower()) is None]
    if not missing:
        return settings, notes
    path = Path(settings.fx_cache)
    cached = _read_cache(path, today, settings.fx_max_age_days)
    snapshot = cached[:2] if cached else None
    origin = "cache"
    if not offline and (cached is None or cached[2] != today):
        try:
            requester = client.get if client else httpx.get
            response = requester(ECB_URL, timeout=10, follow_redirects=False,
                                 headers={"User-Agent": settings.bazos_user_agent})
            response.raise_for_status()
            snapshot = parse_ecb(response.content, today=today, max_age=settings.fx_max_age_days)
            origin = "live"
            try:
                _write_cache(path, *snapshot, today)
            except OSError:
                notes.append("FX: fresh ECB rates loaded, but cache could not be saved")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation, ElementTree.ParseError):
            notes.append("FX: ECB unavailable or invalid; checking dated cache")
    if snapshot is None:
        notes.append(f"STALE_FX: no valid rate for {', '.join(missing)}; those prices cannot be valued")
        return settings, notes
    published, rates = snapshot
    updates = {"eur_" + code.lower(): rates[code] for code in missing}
    notes.append(f"FX: ECB {published.isoformat()} ({origin}); " + ", ".join(
        f"1 EUR = {rates[code]} {code}" for code in missing))
    return settings.model_copy(update=updates), notes
