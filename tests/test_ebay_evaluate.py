import httpx
import pytest

from bazar_deals import ebay_evaluate as module
from bazar_deals.selling.inventory import Inventory, InventoryItem


@pytest.mark.parametrize("enabled", [False, True])
def test_evaluation_requires_enabled_store_and_never_logs_listings(monkeypatch, tmp_path, capsys, enabled):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EBAY_STORE_URL", "https://store.example")
    monkeypatch.setenv("EBAY_STORE_TOKEN", "store-secret-canary")
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-canary")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret-canary")
    monkeypatch.setattr(module, "load_inventory", lambda: Inventory(items=[
        InventoryItem(id="chip", title="MOS 6510", segment="retro", part_numbers=["6510"]) ]))
    payload = {"itemSummaries": [{"itemId": "id-canary", "itemWebUrl": "https://www.ebay.de/itm/123",
        "title": "MOS 6510 chip-canary", "seller": {"username": "seller-canary"},
        "price": {"value": "25", "currency": "EUR"}}]}
    searches = []
    def search(*args, **kwargs):
        searches.append(True)
        return payload
    monkeypatch.setattr(module.EbayBrowseClient, "search_query", search)
    monkeypatch.setattr(module.EbayBrowseClient, "search", search)
    uploads = []
    def handler(request):
        if request.url.path == "/api/credentials":
            return httpx.Response(204)
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"epoch": 7, "enabled": enabled})
        import json
        uploads.append(json.loads(request.content))
        return httpx.Response(200, json={"saved": len(uploads[-1]["records"])})
    original = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if enabled:
        module.evaluate()
        assert uploads[0]["epoch"] == 7
        assert {r["kind"] for r in uploads[0]["records"]} == {"stock_comparison", "buy_candidate"}
        assert all(r["review_status"] == "unreviewed" for r in uploads[0]["records"])
    else:
        with pytest.raises(ValueError, match="not been activated"):
            module.evaluate()
        assert not searches and not uploads
    output = capsys.readouterr()
    assert "canary" not in output.out + output.err
    assert list(tmp_path.iterdir()) == []


def test_rate_limit_keeps_previous_batch_and_does_not_retry(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EBAY_STORE_URL", "https://store.example")
    monkeypatch.setenv("EBAY_STORE_TOKEN", "store-secret-canary")
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-canary")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret-canary")
    monkeypatch.setattr(module, "load_inventory", lambda: Inventory(items=[
        InventoryItem(id="chip", title="MOS 6510", segment="retro", part_numbers=["6510"]) ]))
    searches = []
    def limited(*args, **kwargs):
        searches.append(True)
        request = httpx.Request("GET", "https://api.ebay.com/buy/browse/v1/item_summary/search")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("limited", request=request, response=response)
    monkeypatch.setattr(module.EbayBrowseClient, "search_query", limited)
    uploads = []
    def handler(request):
        if request.url.path == "/api/credentials":
            return httpx.Response(204)
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"epoch": 1, "enabled": True})
        uploads.append(request.url.path)
        return httpx.Response(200, json={"saved": 0})
    original = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))

    module.evaluate()

    assert len(searches) == 1
    assert uploads == []
    assert "rate limited; previous private batch kept" in capsys.readouterr().out
