import httpx
import pytest

from bazar_deals.adapters.ebay import EbayBrowseClient
from bazar_deals.config import Settings
from bazar_deals.ebay_probe import SEARCH_URL, TOKEN_URL, probe
from bazar_deals.selling.collect import collect_ebay


def settings() -> Settings:
    return Settings(_env_file=None, ebay_client_id="test-app", ebay_client_secret="secret-canary",
                    ebay_retention_enabled=False)


def test_regular_client_cannot_fetch_even_with_cached_token(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("no-persistence mode must stop before any network request")

    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)
    client = EbayBrowseClient(settings())
    client._token = "previous-token"
    for action in (lambda: client.search("1"), lambda: client.search_query("nintendo"), client.fetch_new):
        with pytest.raises(RuntimeError, match="no-persistence"):
            action()
    result = collect_ebay("seller-canary", settings())
    assert not result.ok and not result.listings
    assert "no-persistence" in result.reason


def test_stock_comparison_keeps_sk_filter_without_purchase_price_cap(monkeypatch):
    filters = []
    def get(url, **kwargs):
        filters.append(kwargs["params"]["filter"])
        return httpx.Response(200, json={"itemSummaries": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    client = EbayBrowseClient(settings().model_copy(update={"ebay_retention_enabled": True}))
    client._token = "test-token"
    client.search_query("MOS 6510", purchase_budget=False)
    client.search_query("MOS 6510")
    assert "deliveryCountry:SK" in filters[0] and "price:" not in filters[0]
    assert "priceCurrency" not in filters[0]
    assert "deliveryCountry:SK" in filters[1] and "price:" in filters[1]
    assert "priceCurrency:EUR" in filters[1]
    assert "conditions:" not in filters[0] and "conditions:" not in filters[1]


def test_browse_filter_requires_currency_with_price() -> None:
    from bazar_deals.adapters.ebay import browse_filter

    capped = browse_filter(min_price=20, max_price=110)
    assert "price:[20..110]" in capped
    assert "priceCurrency:EUR" in capped
    assert "buyingOptions:{FIXED_PRICE}" in capped
    assert "deliveryCountry:SK" in capped
    assert "conditions:" not in capped
    open_filter = browse_filter()
    assert "price:" not in open_filter
    assert "priceCurrency" not in open_filter


def test_probe_keeps_marketplace_response_out_of_files_and_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    requests = []

    def handle(request):
        requests.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "token-canary"})
        assert str(request.url).startswith(SEARCH_URL)
        assert request.url.params["limit"] == "1"
        assert "deliveryCountry:SK" in request.url.params["filter"]
        return httpx.Response(200, json={"total": 1, "itemSummaries": [
            {"title": "title-canary", "seller": {"username": "seller-canary"}, "itemId": "id-canary"}
        ]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert probe(settings(), client) == 0
    assert len(requests) == 2
    output = capsys.readouterr()
    assert "OAuth: PASS" in output.out and "Browse: PASS" in output.out
    assert "canary" not in output.out + output.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage,status", [("oauth", 401), ("oauth", 302), ("browse", 403), ("browse", 302)])
def test_probe_failures_are_sanitized_and_do_not_follow_redirects(stage, status, capsys):
    calls = []

    def handle(request):
        calls.append(request)
        if str(request.url) == TOKEN_URL and stage == "browse":
            return httpx.Response(200, json={"access_token": "token-canary"})
        return httpx.Response(status, text="seller-canary secret-canary",
                              headers={"Location": "https://example.com/secret-canary"})

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        assert probe(settings(), client) == 1
    assert len(calls) == (1 if stage == "oauth" else 2)
    output = capsys.readouterr()
    assert "FAIL" in output.out and "canary" not in output.out + output.err


def test_fetch_new_keeps_hits_when_a_later_category_is_400(monkeypatch):
    monkeypatch.setattr("bazar_deals.adapters.ebay.hunt_target_queries", lambda: ())
    monkeypatch.setattr("bazar_deals.adapters.ebay._SMALL_CATEGORIES", ("11450", "3213"))

    def get(url, **kwargs):
        params = kwargs["params"]
        request = httpx.Request("GET", url)
        if params.get("category_ids") == "3213":
            return httpx.Response(400, request=request, text="Invalid price filter")
        item = {
            "itemId": "hit-1",
            "title": "Apple iPhone 13 128GB",
            "itemWebUrl": "https://www.ebay.de/itm/hit-1",
            "price": {"value": "55", "currency": "EUR"},
            "buyingOptions": ["FIXED_PRICE"],
            "condition": "USED",
        }
        return httpx.Response(200, json={"itemSummaries": [item]}, request=request)

    monkeypatch.setattr(httpx, "get", get)
    client = EbayBrowseClient(settings().model_copy(update={"ebay_retention_enabled": True}))
    client._token = "test-token"
    found = client.fetch_new()
    assert [item.external_id for item in found] == ["hit-1"]
