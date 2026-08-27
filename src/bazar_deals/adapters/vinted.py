from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Vertical
from bazar_deals.htmlparse import parse_vinted_detail, parse_vinted_items
from bazar_deals.rules import rules

_PROD = "https://pro.svc.vinted.com"
_SANDBOX = "https://pro-public-sandbox.svc.vinted.com"
_VINTED = rules().get("vinted") or {}
_CATALOGS = tuple(str(path) for path in _VINTED.get("catalogs") or ())

NO_PUBLIC_CATALOG = (
    "Vinted Pro Integrations is sell-side only. Catalog hunt uses the public site HTML."
)


def _catalog_url(path: str | None, *, lo: int, hi: int, page: int) -> str:
    base = "https://www.vinted.sk/catalog"
    if path:
        base = f"{base}/{path.lstrip('/')}"
    return f"{base}?order=newest_first&price_from={lo}&price_to={hi}&page={page}"


class VintedHuntClient(ListingSource):
    """Public Vinted catalog pages. No DataDome bypass — polite GET only."""

    marketplace = Marketplace.VINTED.value

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_path: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.fixture_path = fixture_path

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if self.fixture_path:
            return parse_vinted_items(self.fixture_path.read_text(encoding="utf-8"))
        lo = int(self.settings.min_buy_eur)
        hi = int(self.settings.max_buy_eur)
        found: list[Listing] = []
        seen: set[str] = set()
        paths = _CATALOGS or (None,)
        gap = min(0.8, max(0.0, self.settings.bazos_request_gap_seconds))
        for index, path in enumerate(paths):
            if index:
                time.sleep(gap)
            url = _catalog_url(path, lo=lo, hi=hi, page=1)
            response = httpx.get(
                url,
                headers={
                    "User-Agent": self.settings.bazos_user_agent,
                    "Accept": "text/html",
                },
                timeout=30.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            batch = parse_vinted_items(response.text)
            for item in batch:
                key = item.external_id or str(item.url)
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
        return found

    def enrich_listing(self, listing: Listing) -> Listing:
        if self.fixture_path:
            return listing
        try:
            response = httpx.get(
                str(listing.url),
                headers={
                    "User-Agent": self.settings.bazos_user_agent,
                    "Accept": "text/html",
                },
                timeout=30.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            detail = parse_vinted_detail(response.text)
        except httpx.HTTPError:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        raw = dict(listing.raw)
        raw["detail_fetched"] = bool(detail)
        return listing.model_copy(update={"description": detail, "raw": raw})


def sign_vinted_request(
    *,
    method: str,
    path: str,
    access_key: str,
    signing_key: str,
    body: str = "",
    timestamp: int | None = None,
) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    payload = f"{ts}.{method.upper()}.{path}.{access_key}.{body}"
    digest = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={digest}"


class VintedProClient(ListingSource):
    """Official Vinted Pro Integrations: sell/sync own inventory, not catalog hunt."""

    marketplace = Marketplace.VINTED.value

    def __init__(self, settings: Settings | None = None, *, sandbox: bool = False) -> None:
        self.settings = settings or Settings()
        self.base = _SANDBOX if sandbox else _PROD

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        raise RuntimeError(NO_PUBLIC_CATALOG)

    def list_own_items(self, *, page: int = 1, per_page: int = 100) -> dict:
        return self._request("GET", f"/api/v1/items?page={page}&per_page={per_page}")

    def create_items(self, payload: dict) -> dict:
        return self._request("POST", "/api/v1/items", json_body=payload)

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        access_key = self.settings.vinted_access_key
        signing_key = self.settings.vinted_signing_key
        if not access_key or not signing_key:
            raise RuntimeError(
                "Set VINTED_ACCESS_KEY and VINTED_SIGNING_KEY after Vinted Pro allowlist"
            )
        body = "" if json_body is None else json.dumps(json_body, separators=(", ", ": "))
        parsed = urlparse(self.base + path)
        signature = sign_vinted_request(
            method=method,
            path=parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            access_key=access_key,
            signing_key=signing_key,
            body=body,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Vpi-Access-Key": access_key,
            "X-Vpi-Hmac-Sha256": signature,
        }
        response = httpx.request(
            method,
            self.base + path,
            headers=headers,
            content=body.encode("utf-8") if body else None,
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json() if response.content else {}
