"""Collect real eBay evaluation data exclusively into the deletable private store."""
from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

import httpx

from bazar_deals.adapters.ebay import EbayBrowseClient, _SMALL_CATEGORIES
from bazar_deals.config import Settings
from bazar_deals.selling.demand import _glossary_name, _german_locality, best_item, is_want_to_buy, queries_for
from bazar_deals.selling.inventory import load_inventory


def evaluate(*, configure_only=False):
    settings = Settings()
    base = os.environ["EBAY_STORE_URL"].rstrip("/")
    if urlparse(base).scheme != "https" or urlparse(base).username or urlparse(base).query:
        raise ValueError("invalid store URL")
    headers = {"Authorization": "Bearer " + os.environ["EBAY_STORE_TOKEN"]}
    with httpx.Client(timeout=25, follow_redirects=False) as store:
        response = store.post(base + "/api/credentials", headers=headers,
            json={"client_id": settings.ebay_client_id, "client_secret": settings.ebay_client_secret})
        response.raise_for_status()
        if configure_only:
            print("eBay notification verifier: credentials configured")
            return
        response = store.get(base + "/api/status", headers=headers)
        response.raise_for_status()
        status = response.json()
        if not status["enabled"]:
            raise ValueError("retention has not been activated")
        # Regular CLI imports remain disabled. Only this private-store path is enabled.
        browse = EbayBrowseClient(settings.model_copy(update={"ebay_retention_enabled": True}))
        inventory = load_inventory()
        records = {}

        def ingest(payload, kind, query):
            for item in payload.get("itemSummaries", []):
                if not item.get("itemId") or not item.get("itemWebUrl"):
                    continue
                hit = best_item(str(item.get("title", "")), inventory.items) if kind == "stock_comparison" else None
                if kind == "stock_comparison" and hit is None:
                    continue
                seller = item.get("seller") or {}
                if not seller.get("username"):
                    continue
                price = item.get("price") or {}
                shipping = [option.get("shippingCost") for option in item.get("shippingOptions", [])]
                stock_id = hit[0].id if hit else ""
                record = {
                    "kind": "wanted_match" if hit and is_want_to_buy(item.get("title", "")) else kind,
                    "query": query, "stock_id": stock_id,
                    "item_id": item["itemId"], "title": str(item.get("title", "")), "url": item["itemWebUrl"],
                    "seller": seller["username"], "seller_id": seller.get("userId", ""),
                    "price": price.get("value"), "currency": price.get("currency"),
                    "shipping": "; ".join(f"{s['value']} {s['currency']}" for s in shipping if isinstance(s, dict) and "value" in s and "currency" in s) or "unknown",
                    "sk_delivery_filter": True, "review_status": "unreviewed",
                }
                records[(kind, item["itemId"], stock_id)] = record

        seen = set()
        for item in inventory.items:
            query = next(iter(queries_for(item)), "")
            if item.species:
                species = _glossary_name(item.species[0], "en") or item.species[0]
                place = _german_locality(item) or (item.locality or item.origin).split(",")[-1].strip()
                query = f"{species} {place}".strip()
            if not query or query.casefold() in seen:
                continue
            seen.add(query.casefold())
            # Existing stock can sell below our purchase floor or above the hunt
            # cap. Applying that budget here would censor the market comparison.
            ingest(browse.search_query(query, limit=20, purchase_budget=False), "stock_comparison", query)
        for category in _SMALL_CATEGORIES[:8]:
            ingest(browse.search(category, limit=20), "buy_candidate", str(category))
        # No local listing dumps, comps-cache writes, AI exports or GitHub comments.
        response = store.post(base + "/api/batches", headers=headers,
                              json={"epoch": status["epoch"], "records": list(records.values())})
        response.raise_for_status()
        print(f"eBay evaluation: stored {response.json()['saved']} records in the private deletable store")
        print("eBay evaluation: active listings are comparisons/candidates, not confirmed BUY or buyers")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure-only", action="store_true")
    args = parser.parse_args()
    try:
        evaluate(configure_only=args.configure_only)
    except Exception:
        # Never expose marketplace responses, tokens or exception request URLs.
        print("eBay evaluation: FAILED; no response details were logged")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
