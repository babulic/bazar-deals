from datetime import date
from decimal import Decimal
import json

import httpx
import pytest

from bazar_deals.config import Settings
from bazar_deals.domain import Money, Listing, Marketplace
from bazar_deals.fx import ECB_URL, parse_ecb, prepare_exchange_rates
from bazar_deals.pipeline import _to_eur, score_listings
from bazar_deals.selling.collect import collect_aukro
from bazar_deals.selling.demand import _search_aukro

TODAY = date(2026, 8, 31)


def xml(day="2026-08-28", czk="25", pln="4"):
    return f'''<?xml version="1.0"?><gesmes:Envelope
        xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
        xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
        <Cube><Cube time="{day}"><Cube currency="CZK" rate="{czk}"/>
        <Cube currency="PLN" rate="{pln}"/></Cube></Cube></gesmes:Envelope>'''.encode()


def settings(tmp_path, **kwargs):
    return Settings(fx_cache=str(tmp_path / "fx.json"), **kwargs)


def test_live_snapshot_used_once_per_day_for_both_currencies(tmp_path):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=xml())
    original = settings(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first, notes = prepare_exchange_rates(original, client=client, today=TODAY)
        second, cached_notes = prepare_exchange_rates(original, client=client, today=TODAY)
    assert calls == [ECB_URL]
    assert first.eur_czk == second.eur_czk == 25
    assert first.eur_pln == second.eur_pln == 4
    assert original.eur_czk is None
    assert "2026-08-28" in notes[-1] and "live" in notes[-1]
    assert "cache" in cached_notes[-1]
    assert json.loads((tmp_path / "fx.json").read_text())["checked_on"] == "2026-08-31"


def test_next_day_refreshes_snapshot_and_converts_price_and_shipping(tmp_path):
    (tmp_path / "fx.json").write_text(json.dumps({
        "source": ECB_URL, "published": "2026-08-28", "checked_on": "2026-08-30",
        "rates": {"CZK": "25", "PLN": "4"}}))
    def handler(request):
        return httpx.Response(200, content=xml("2026-08-31", "20", "5"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = prepare_exchange_rates(settings(tmp_path), client=client, today=TODAY)
    item = Listing(marketplace=Marketplace.SBAZAR, external_id="1", title="Console",
                   url="https://www.sbazar.cz/inzerat/1", price=Money(amount=1000, currency="CZK"),
                   shipping_cost=Money(amount=100, currency="PLN"))
    converted = _to_eur(item, result.eur_czk, result.eur_pln)
    assert converted.price.amount == 50
    assert converted.shipping_cost.amount == 20


def test_network_failure_uses_only_nonstale_published_rates(tmp_path):
    path = tmp_path / "fx.json"
    path.write_text(json.dumps({"source": ECB_URL, "published": "2026-08-28", "checked_on": "2026-08-28",
                                "rates": {"CZK": "25", "PLN": "4"}}))
    def handler(request):
        raise httpx.ConnectError("offline", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        good, notes = prepare_exchange_rates(settings(tmp_path), client=client, today=TODAY)
        bad, stale_notes = prepare_exchange_rates(settings(tmp_path), client=client, today=date(2026, 9, 5))
    assert good.eur_czk == 25 and good.eur_pln == 4
    assert any("unavailable" in note for note in notes)
    assert bad.eur_czk is None and bad.eur_pln is None
    assert "cannot be valued" in stale_notes[-1]


@pytest.mark.parametrize("body", [
    xml("2026-08-23"), xml("2026-09-01"), xml(czk="0"), xml(pln="-1"),
    xml(czk="NaN"), xml(pln="Infinity"), b"<html>error</html>", b"broken XML",
    xml().replace(b'currency="PLN"', b'currency="USD"'),
    b'<!DOCTYPE x [<!ENTITY x "bad">]><x/>',
])
def test_invalid_feed_cannot_create_a_rate(tmp_path, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
        result, notes = prepare_exchange_rates(settings(tmp_path), client=client, today=TODAY)
    assert result.eur_czk is None and result.eur_pln is None
    assert not (tmp_path / "fx.json").exists()
    assert "cannot be valued" in notes[-1]


def test_offline_never_requests_rates_and_keeps_explicit_override(tmp_path):
    def handler(request):
        pytest.fail("Offline FX must not request the network")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, notes = prepare_exchange_rates(settings(tmp_path, eur_czk=Decimal("23")),
                                                offline=True, client=client, today=TODAY)
    assert result.eur_czk == 23
    assert result.eur_pln is None
    assert "manual EUR_CZK=23" in notes[0]


def test_one_manual_currency_does_not_disable_the_other(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=xml()))) as client:
        result, _ = prepare_exchange_rates(settings(tmp_path, eur_czk=Decimal("23")), client=client, today=TODAY)
    assert result.eur_czk == 23 and result.eur_pln == 4


@pytest.mark.parametrize("payload", ["[]", "null", "{}", "not json"])
def test_corrupt_cache_is_ignored_offline(tmp_path, payload):
    (tmp_path / "fx.json").write_text(payload)
    result, _ = prepare_exchange_rates(settings(tmp_path), offline=True, today=TODAY)
    assert result.eur_czk is None


def test_missing_czk_cannot_reach_scoring_or_overwrite_inventory(tmp_path, monkeypatch):
    item = Listing(marketplace=Marketplace.SBAZAR, external_id="1", title="Console",
                   url="https://www.sbazar.cz/inzerat/1", price=Money(amount=1000, currency="CZK"))
    run = score_listings([item], settings(tmp_path), object())
    assert run.funnel["invalid_price"] == 1 and not run.deals
    payload = {"content": [{"itemId": 1, "itemName": "Console", "buyNowPrice": {"amount": 1000, "currency": "CZK"}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json=payload, request=httpx.Request("POST", "https://example.test")))
    result = collect_aukro(1, settings(tmp_path))
    assert not result.ok and not result.listings
    assert "previous inventory retained" in result.reason


def test_aukro_demand_uses_shared_rate_and_missing_rate_means_unknown_budget(tmp_path):
    payload = {"content": [{"itemId": 1, "itemName": "Koupím Nintendo", "buyNowPrice": {"amount": 1000, "currency": "CZK"}}]}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        ads, _ = _search_aukro("koupím", settings(tmp_path, eur_czk=Decimal("20")), client=client)
        unknown, _ = _search_aukro("koupím", settings(tmp_path), client=client)
    assert ads[0].offer_eur == 50
    assert unknown[0].offer_eur is None


def test_missing_or_invalid_czk_never_means_one_to_one():
    for rate in (None, Decimal("0"), Decimal("-25")):
        with pytest.raises(ValueError):
            Money(amount=1000, currency="CZK").to_eur(rate)
    assert Settings(eur_czk="").eur_czk is None
