from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

import httpx

from bazar_deals.adapters.base import ListingSource
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Vertical

_PROD = "https://pro.svc.vinted.com"
_SANDBOX = "https://pro-public-sandbox.svc.vinted.com"

NO_PUBLIC_CATALOG = (
    "Vinted has no public catalog API. Official Vinted Pro Integrations is "
    "allowlisted and sell-side (own items, orders, webhooks). "
    "Do not use the undocumented /api/v2 catalog endpoints or DataDome bypasses."
)


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

    def list_own_items(self) -> dict:
        return self._request("GET", "/api/v1/items")

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
