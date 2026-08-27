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
from bazar_deals.htmlparse import parse_vinted_catalog_payload, parse_vinted_detail, parse_vinted_items
from bazar_deals.rules import rules

_PROD = "https://pro.svc.vinted.com"
_SANDBOX = "https://pro-public-sandbox.svc.vinted.com"
_VINTED = rules().get("vinted") or {}
_CATALOGS = tuple(str(path) for path in _VINTED.get("catalogs") or ())
_HOST = "https://www.vinted.sk"
_CATALOG_API = f"{_HOST}/api/v2/catalog/items"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

NO_PUBLIC_CATALOG = (
    "Vinted Pro Integrations is sell-side only. Catalog hunt uses the public site HTML."
)
VINTED_BLOCKED = (
    "Vinted catalog blocked (DataDome/captcha). Hunt uses public HTML/JSON only — "
    "VINTED_ACCESS_KEY is sell-side Pro, not catalog search"
)


def vinted_catalog_blocked(response: httpx.Response) -> bool:
    """True for a challenge/error page, not for a normal catalog that also loads DataDome JS."""
    if response.status_code in {401, 403, 429, 503}:
        return True
    snippet = (response.text or "")[:12000].casefold()
    return any(
        marker in snippet
        for marker in (
            "captcha-delivery",
            "geo.captcha-delivery.com",
            "please enable js and disable any ad blocker",
        )
    )


def _catalog_url(path: str | None, *, lo: int, hi: int, page: int) -> str:
    base = f"{_HOST}/catalog"
    if path:
        base = f"{base}/{path.lstrip('/')}"
    return f"{base}?order=newest_first&price_from={lo}&price_to={hi}&page={page}"


def _catalog_id(path: str | None) -> str | None:
    if not path:
        return None
    token = path.split("-", 1)[0].strip()
    return token if token.isdigit() else None


class VintedHuntClient(ListingSource):
    """Public Vinted catalog via the same JSON the site uses after an anon session."""

    marketplace = Marketplace.VINTED.value

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_path: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.fixture_path = fixture_path
        self._client = client

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        if self.fixture_path:
            return parse_vinted_items(self.fixture_path.read_text(encoding="utf-8"))
        lo = int(self.settings.min_buy_eur)
        hi = int(self.settings.max_buy_eur)
        found: list[Listing] = []
        seen: set[str] = set()
        paths = _CATALOGS or (None,)
        gap = min(0.4, max(0.0, self.settings.bazos_request_gap_seconds))
        owned = self._client is None
        client = self._client or httpx.Client(
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        try:
            self._warmup(client)
            for index, path in enumerate(paths):
                if index:
                    time.sleep(gap)
                batch = self._fetch_catalog(client, path, lo=lo, hi=hi)
                for item in batch:
                    key = item.external_id or str(item.url)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(item)
        finally:
            if owned:
                client.close()
        if not found:
            raise RuntimeError(VINTED_BLOCKED)
        return found

    def enrich_listing(self, listing: Listing) -> Listing:
        if self.fixture_path:
            return listing
        owned = self._client is None
        client = self._client or httpx.Client(
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
                "Accept": "text/html",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        try:
            response = client.get(str(listing.url))
            response.raise_for_status()
            detail = parse_vinted_detail(response.text)
        except httpx.HTTPError:
            raw = dict(listing.raw)
            raw["detail_fetched"] = False
            return listing.model_copy(update={"raw": raw})
        finally:
            if owned:
                client.close()
        raw = dict(listing.raw)
        raw["detail_fetched"] = bool(detail)
        return listing.model_copy(update={"description": detail, "raw": raw})

    def _warmup(self, client: httpx.Client) -> None:
        try:
            client.get(_HOST + "/", headers={"Accept": "text/html"})
        except httpx.HTTPError:
            return

    def _fetch_catalog(
        self, client: httpx.Client, path: str | None, *, lo: int, hi: int
    ) -> list[Listing]:
        catalog_url = _catalog_url(path, lo=lo, hi=hi, page=1)
        items = self._fetch_api(client, path, lo=lo, hi=hi, referer=catalog_url)
        if items:
            return items
        response = client.get(catalog_url, headers={"Accept": "text/html"})
        if vinted_catalog_blocked(response):
            return []
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return parse_vinted_items(response.text or "")

    def _fetch_api(
        self,
        client: httpx.Client,
        path: str | None,
        *,
        lo: int,
        hi: int,
        referer: str,
    ) -> list[Listing]:
        params: dict[str, str | int] = {
            "order": "newest_first",
            "price_from": lo,
            "price_to": hi,
            "per_page": 96,
            "page": 1,
        }
        catalog_id = _catalog_id(path)
        if catalog_id:
            params["catalog_ids"] = catalog_id
        try:
            response = client.get(
                _CATALOG_API,
                params=params,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": referer,
                },
            )
        except httpx.HTTPError:
            return []
        if vinted_catalog_blocked(response):
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        if not isinstance(payload, dict):
            return []
        return parse_vinted_catalog_payload(payload)


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
