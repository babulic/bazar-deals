import base64

import httpx
import pytest

from bazar_deals.adapters.allegro_auth import AllegroAuth
from bazar_deals.adapters.central_europe import CentralEuropeClient
from bazar_deals.config import Settings


def settings(**updates):
    return Settings(allegro_client_id='app', allegro_client_secret='secret', **updates)


def response(token='token-one', **updates):
    return {'access_token': token, 'expires_in': 43199, 'token_type': 'bearer', **updates}


def test_credentials_alone_do_not_enable_listing_search():
    def fail(request):
        pytest.fail('Unconfirmed entitlement must not initiate OAuth or search')
    client = CentralEuropeClient('allegro_pl', settings(), client=httpx.Client(transport=httpx.MockTransport(fail)))
    assert client.fetch_new() == []
    assert 'ACCESS_NOT_GRANTED' in client.notes[0]


def test_automatic_token_reused_and_refreshed_on_expiry(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr('bazar_deals.adapters.allegro_auth.time.monotonic', lambda: clock[0])
    def handle(request):
        calls.append(request)
        assert str(request.url) == 'https://allegro.pl/auth/oauth/token'
        assert request.headers['Authorization'] == 'Basic ' + base64.b64encode(b'app:secret').decode()
        assert request.content == b'grant_type=client_credentials'
        assert 'bazar-deals' in request.headers['User-Agent']
        return httpx.Response(200, json=response(token=f'token-{len(calls)}', expires_in=100))
    auth = AllegroAuth(settings(allegro_listing_access_confirmed=True), httpx.Client(transport=httpx.MockTransport(handle)))
    assert auth.token() == auth.token() == 'token-1'
    clock[0] += 91
    assert auth.token() == 'token-2'
    assert len(calls) == 2


@pytest.mark.parametrize('code,expected_posts,expected_gets', [(401,2,2), (403,1,1), (429,1,1)])
def test_failed_api_refresh_is_bounded_and_never_retries_permissions(code, expected_posts, expected_gets):
    calls = {'POST':0,'GET':0}
    def handle(request):
        calls[request.method] += 1
        if request.method == 'POST':
            return httpx.Response(200, json=response())
        return httpx.Response(code, json={'secret': 'must-not-appear'})
    client = CentralEuropeClient('allegro_pl', settings(allegro_listing_access_confirmed=True), client=httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(RuntimeError) as error:
        client.search('Nintendo')
    assert 'must-not-appear' not in str(error.value)
    assert calls == {'POST':expected_posts, 'GET':expected_gets}


def test_one_refresh_recovers_401_and_retains_sk_filter():
    calls = {'POST':0,'GET':0}
    def handle(request):
        calls[request.method] += 1
        if request.method == 'POST':
            return httpx.Response(200, json=response(token=f'token-{calls["POST"]}'))
        if calls['GET'] == 1:
            return httpx.Response(401)
        assert request.headers['Authorization'] == 'Bearer token-2'
        assert request.url.params['shipping.country'] == 'SK'
        assert request.url.params['currency'] == 'EUR'
        return httpx.Response(200, json={'items': {'regular': [], 'promoted': []}})
    client = CentralEuropeClient('allegro_sk', settings(allegro_listing_access_confirmed=True), client=httpx.Client(transport=httpx.MockTransport(handle)))
    assert client.search('Nintendo') == []
    assert calls == {'POST':2,'GET':2}


@pytest.mark.parametrize('payload', [[], {}, response(expires_in=0), response(expires_in=True),
    response(expires_in='NaN'), response(token='bad\ntoken'), response(token_type='unknown')])
def test_invalid_oauth_payload_is_sanitized(payload):
    auth = AllegroAuth(settings(allegro_listing_access_confirmed=True), httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))))
    with pytest.raises(RuntimeError, match='invalid Allegro OAuth response'):
        auth.token()


def test_oauth_redirect_never_sends_secrets_to_other_host():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location':'https://evil.example/'})
    auth = AllegroAuth(settings(allegro_listing_access_confirmed=True), httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(RuntimeError, match='HTTP 302'):
        auth.token()
    assert len(calls) == 1


def test_static_token_remains_supported_and_secrets_hidden():
    s = Settings(allegro_access_token='private-token', allegro_client_secret='private-secret')
    assert AllegroAuth(s).token() == 'private-token'
    assert 'private-token' not in repr(s)
    assert 'private-secret' not in repr(s)
