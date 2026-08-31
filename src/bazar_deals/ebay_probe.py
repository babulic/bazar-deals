"""Bounded eBay activation check: response data stays in process memory only.

No listing models, files, caches, reports, GitHub comments or response-body logs.
Only fixed technical status messages leave this module.
"""
from __future__ import annotations

import logging

import httpx

from bazar_deals.config import Settings

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def probe(settings: Settings, client: httpx.Client) -> int:
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        print("eBay probe: missing credentials")
        return 1
    try:
        token_response = client.post(
            TOKEN_URL,
            auth=(settings.ebay_client_id, settings.ebay_client_secret),
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            follow_redirects=False,
        )
        if token_response.status_code != 200:
            print(f"eBay OAuth: FAIL (HTTP {token_response.status_code})")
            return 1
        token = token_response.json().get("access_token")
        if not isinstance(token, str) or not token:
            print("eBay OAuth: FAIL (invalid response)")
            return 1
        print("eBay OAuth: PASS")
        response = client.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_DE"},
            params={"q": "nintendo", "limit": "1", "filter": "buyingOptions:{FIXED_PRICE},deliveryCountry:SK"},
            follow_redirects=False,
        )
        if response.status_code != 200:
            print(f"eBay Browse: FAIL (HTTP {response.status_code})")
            return 1
        payload = response.json()
        if not isinstance(payload, dict) or type(payload.get("total")) is not int:
            print("eBay Browse: FAIL (invalid response)")
            return 1
        # Do not print item counts, titles, sellers, prices, URLs or response bodies.
        print("eBay Browse: PASS (SK delivery filter requested; response not retained)")
        return 0
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # Exception messages can contain URLs, headers or response content.
        print("eBay probe: FAIL (network or response error; details withheld)")
        return 1


def main() -> int:
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").disabled = True
    try:
        settings = Settings()
    except Exception:
        print("eBay probe: FAIL (invalid configuration; details withheld)")
        return 1
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        return probe(settings, client)


if __name__ == "__main__":
    raise SystemExit(main())
