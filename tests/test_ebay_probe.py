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
