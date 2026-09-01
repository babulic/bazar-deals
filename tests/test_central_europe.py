import html
import json
from decimal import Decimal

import httpx
import pytest

from bazar_deals.adapters.central_europe import (
    CentralEuropeClient, HUNT_SITES, SITES, delivery_to_sk, parse_public_listings,
)
from bazar_deals.cli import _dump_listings, _load_listings, _sources
from bazar_deals.config import Settings
from bazar_deals.domain import Action, Listing, Marketplace, Money
from bazar_deals.pipeline import hunt_sources, score_listings
from bazar_deals.selling.demand import find_buyers, searched_sites
from bazar_deals.selling.inventory import Inventory, InventoryItem
from bazar_deals.soldcomps import SoldComp, SoldCompClient


def ld(*, source="olx", description="", shipping=None, availability="InStock", price="215", currency="PLN"):
    offer = {"@type": "Offer", "url": f"https://www.{SITES[source]}/item/123", "price": price,
             "priceCurrency": currency, "availability": f"https://schema.org/{availability}"}
    if shipping is not None:
        offer["shippingDetails"] = shipping
    product = {"@type": "Product", "name": "Nintendo Switch V2", "description": description, "offers": offer}
    return '<script type="application/ld+json">' + json.dumps(product) + '</script>'


def astro(value):
    if isinstance(value, dict):
        return [0, {key: astro(item) for key, item in value.items()}]
    if isinstance(value, list):
        return [1, [astro(item) for item in value]]
    return [0, value]


def sbazar(rows, detail=False):
    props = {"offer": rows[0]} if detail else {"offers": {"results": rows}}
    encoded = {key: astro(value) for key, value in props.items()}
    return '<astro-island props="' + html.escape(json.dumps(encoded), quote=True) + '"></astro-island>'


def sbazar_row(**updates):
    return {"id": 123, "name": "Nintendo Switch V2", "seoName": "123-nintendo-switch-v2",
            "price": 1225, "description": "Plně funkční Nintendo Switch V2 s nabíječkou. Posílám na Slovensko.",
            **updates}


@pytest.mark.parametrize("text", [
    "Posílám na Slovensko.", "Zasielam aj na Slovensko.", "Wysyłam na Słowację.", "Shipping to Slovakia.",
])
def test_explicit_delivery(text):
    assert delivery_to_sk(text) is True


@pytest.mark.parametrize("text", [
    "Slovensko", "Zásilkovna", "Przesyłka OLX", "Neposílám na Slovensko.",
    "Shipping to Slovakia?", "Do you offer shipping to Slovakia", "No shipping to Slovakia.",
    "Buyer requested shipping to Slovakia.", "Posílám na Slovensko. Pouze osobní odběr.",
    "If you arrange shipping to Slovakia", "Posílám pouze po ČR.",
])
def test_ambiguous_negative_or_domestic_delivery_never_passes(text):
    assert delivery_to_sk(text) is not True


def test_sbazar_astro_search_and_detail_ignore_recommendations():
    rows = parse_public_listings(sbazar([sbazar_row(description=None)]), "sbazar")
    assert len(rows) == 1
    assert rows[0].ships_to_slovakia is None
    assert rows[0].price.currency == "CZK"
    assert str(rows[0].url) == "https://www.sbazar.cz/inzerat/123-nintendo-switch-v2"
    rows = parse_public_listings(sbazar([sbazar_row()], detail=True), "sbazar")
    assert rows[0].ships_to_slovakia is True
    assert not parse_public_listings(sbazar([sbazar_row(sold=True)]), "sbazar")[0].buy_now


def test_structured_delivery_is_country_specific_and_preserves_cost():
    detail = {"shippingDestination": {"addressCountry": "SK"},
              "shippingRate": {"value": "43", "currency": "PLN"}}
    item = parse_public_listings(ld(shipping=detail), "olx")[0]
    assert item.ships_to_slovakia is True
    assert item.shipping_cost.amount == 43
    for bad in [
        {**detail, "shippingDestination": {"addressCountry": "PL"}},
        {**detail, "doesNotShip": True},
        {**detail, "shippingDestination": {"addressCountry": "SK", "postalCode": "81101"}},
    ]:
        assert parse_public_listings(ld(shipping=bad), "olx")[0].ships_to_slovakia is not True
    assert not parse_public_listings(ld(availability="SoldOut"), "olx")[0].buy_now
    assert parse_public_listings(ld().replace("www.olx.pl", "evil.example"), "olx") == []


def test_allegro_uses_official_sk_filter_and_eur_for_both_markets():
    def handler(request):
        assert request.url.host == "api.allegro.pl"
        assert request.url.params["shipping.country"] == "SK"
        assert request.url.params["currency"] == "EUR"
        assert request.url.params["sellingMode.format"] == "BUY_NOW"
        assert request.url.params["marketplaceId"] in {"allegro-pl", "allegro-sk"}
        row = {"id": "123", "name": "Nintendo Switch V2", "sellingMode": {
            "format": "BUY_NOW", "price": {"amount": "50", "currency": "EUR"}}}
        return httpx.Response(200, json={"items": {"regular": [row], "promoted": [row]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for source in ("allegro_pl", "allegro_sk"):
            rows = CentralEuropeClient(source, Settings(allegro_access_token="test"), client=client).search("nintendo")
            assert len(rows) == 1
            assert rows[0].ships_to_slovakia is True
            assert rows[0].shipping_cost is None  # PL's lowest postage must not become SK postage.


def test_missing_allegro_credentials_and_blocked_public_pages_report_unavailable():
    with pytest.raises(RuntimeError, match="ALLEGRO_ACCESS_TOKEN"):
        CentralEuropeClient("allegro_pl", Settings()).search("lego")
    for status in (302, 403, 429):
        def handler(request):
            return httpx.Response(status, headers={"Location": "https://www.facebook.com/login"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            source = CentralEuropeClient("facebook", Settings(), client=client)
            run = hunt_sources([source], settings=Settings(), sold=object(), score=False)
            assert not run.listings
            assert any("LOGIN_REQUIRED" in note for note in source.notes)
            assert not any("facebook:" in note for note in run.fetch_notes)


def test_pln_needs_explicit_conversion_and_unknown_currency_is_not_eur():
    money = Money(amount=215, currency="PLN")
    with pytest.raises(ValueError, match="EUR_PLN"):
        money.to_eur(Decimal("25"))
    assert money.to_eur(Decimal("25"), eur_pln=Decimal("4.3")) == Decimal("50.00")
    with pytest.raises(ValueError, match="Unsupported"):
        Money(amount=100, currency="HUF").to_eur(Decimal("25"))
    assert Settings(eur_pln="").eur_pln is None
    with pytest.raises(ValueError):
        Settings(eur_pln="0")


class Comps:
    def median_sold(self, *args, **kwargs):
        return SoldComp(median=Decimal("200"), sample=8, label="fixture", reliable_for_buy=True)


def test_pipeline_checks_delivery_and_converts_detail_prices_before_buy():
    initial = parse_public_listings(sbazar([sbazar_row(description=None)]), "sbazar")[0]
    settings = Settings(eur_czk=Decimal("24.5"))
    assert not score_listings([initial], settings, Comps()).deals
    def handler(request):
        return httpx.Response(200, text=sbazar([sbazar_row()], detail=True))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = CentralEuropeClient("sbazar", settings, client=client)
        run = score_listings([initial], settings, Comps(), enrichers={Marketplace.SBAZAR: source})
    assert run.deals[0].action == Action.BUY
    assert run.deals[0].costs.buy_price == 50
    assert run.deals[0].item.listing.price.currency == "EUR"
    for value in (False, None):
        candidate = initial.model_copy(update={"ships_to_slovakia": value})
        run = score_listings([candidate], settings, Comps())
        assert run.funnel["no_sk_delivery"] == 1
        assert not run.deals


def test_pln_pipeline_does_not_treat_215_pln_as_215_or_free_eur():
    item = parse_public_listings(ld(description="Working console with charger. Shipping to Slovakia."), "olx")[0]
    run = score_listings([item], Settings(eur_pln=Decimal("4.3")), Comps())
    assert run.deals[0].costs.buy_price == 50
    run = score_listings([item], Settings(), Comps())
    assert run.funnel["invalid_price"] == 1
    assert not run.deals


def test_unverified_new_marketplaces_do_not_seed_price_book(tmp_path):
    book = SoldCompClient(Settings(comps_db=str(tmp_path / "comps.sqlite")))
    item = parse_public_listings(ld(), "olx")[0]
    book.seed_asking([item])
    assert book._asking_catalog == []


def test_all_sources_are_registered_and_offline_stays_offline():
    assert set(HUNT_SITES) <= {s.marketplace for s in _sources("all", Settings(), fixture=None)}
    assert {s.marketplace for s in _sources("all", Settings(), fixture=None)} == {
        "bazos",
        "aukro",
        "vinted",
        "sbazar",
    }
    assert "sbazar.cz" in searched_sites()
    assert "facebook.com" not in searched_sites()
    assert [s.marketplace for s in _sources("all", Settings(), fixture="unused")] == ["bazos"]


def test_fetch_cache_keeps_unavailable_notes(tmp_path):
    target = tmp_path / "fetch.json"
    _dump_listings(target, [], notes=["facebook: login required"])
    assert _load_listings(target) == []
    assert json.loads(target.with_suffix(".notes.json").read_text()) == ["facebook: login required"]


def test_sell_matches_real_demand_and_not_sales_on_new_sources(monkeypatch):
    import bazar_deals.selling.demand as demand
    for name in ("_BAZOS_PHRASES",):
        monkeypatch.setattr(demand, name, {})
    for name in ("_AUKRO_PHRASES", "_VINTED_SITES", "_KA_PHRASES", "_WILLHABEN_PHRASES", "_DELCAMPE_PHRASES", "_FORUM64_PHRASES"):
        monkeypatch.setattr(demand, name, ())
    def search(self, query):
        return [Listing(marketplace=Marketplace(self.marketplace), external_id=str(index),
                        title=title, url=f"https://{SITES[self.marketplace]}/123", price=Money(amount=50))
                for index, title in enumerate(["Kupię Nintendo Switch V2", "Sprzedam Nintendo Switch V2"])]
    monkeypatch.setattr(CentralEuropeClient, "search", search)
    monkeypatch.setattr(CentralEuropeClient, "manual_mode", lambda self: None)
    inventory = Inventory(items=[InventoryItem(id="switch", segment="retro", title="Nintendo Switch V2", part_numbers=["Switch"])])
    digest = find_buyers(inventory, Settings())
    assert {row.want.site for row in digest.matches} == set(SITES.values())
    assert all(row.want.title.startswith("Kupię") for row in digest.matches)


def test_want_ads_are_not_purchase_offers():
    for title in ("Koupím Nintendo Switch", "Kupię Nintendo Switch", "WTB Nintendo Switch"):
        assert not parse_public_listings(sbazar([sbazar_row(name=title)]), "sbazar")[0].buy_now


def test_same_allegro_offer_on_two_domains_is_one_comparable(tmp_path):
    book = SoldCompClient(Settings(comps_db=str(tmp_path / "comps.sqlite")))
    rows = [Listing(marketplace=Marketplace(source), external_id="123", title="Nintendo Switch V2",
                    url=f"https://{SITES[source]}/oferta/123", price=Money(amount=100), ships_to_slovakia=True)
            for source in ("allegro_pl", "allegro_sk")]
    book.seed_asking(rows)
    assert len(book._asking_catalog) == 1


def test_priceless_demand_is_kept_for_sell_but_cannot_be_bought():
    item = parse_public_listings(sbazar([sbazar_row(name="Koupím Nintendo Switch", price=None)]), "sbazar")[0]
    assert item.price.amount == 0
    assert item.buy_now is False
    assert not score_listings([item], Settings(), Comps()).deals


def test_explicit_structured_delivery_exclusion_overrides_description():
    body = ld(description="Shipping to Slovakia.", shipping={
        "shippingDestination": {"addressCountry": "SK"}, "doesNotShip": True})
    assert parse_public_listings(body, "olx")[0].ships_to_slovakia is False


def test_allegro_detail_slug_matches_api_offer_id():
    body = ld(source="allegro_pl", currency="EUR").replace('/item/123', '/oferta/nintendo-switch-123')
    assert parse_public_listings(body, "allegro_pl")[0].external_id == "123"


def empty_sbazar():
    props = {'offers': astro({'results': [], 'pagination': {'total': 0, 'limit': 58}})}
    return '<astro-island props="' + html.escape(json.dumps(props), quote=True) + '"></astro-island>'


@pytest.mark.parametrize('status', [200, 404])
def test_sbazar_explicit_empty_search_is_not_blocked(status):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=empty_sbazar()))) as client:
        source = CentralEuropeClient('sbazar', Settings(), client=client)
        assert source.search('koupím ametyst Brandberg') == []
        assert 'READY: no matches' in source.notes[0]


@pytest.mark.parametrize('status,body', [(404, '<h1>Not found</h1>'), (403, empty_sbazar()), (429, empty_sbazar()), (404, sbazar([]))])
def test_errors_without_explicit_zero_result_search_remain_errors(status, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))) as client:
        with pytest.raises(RuntimeError, match=f'HTTP {status}'):
            CentralEuropeClient('sbazar', Settings(), client=client).search('koupím')


def test_sbazar_fetch_continues_after_empty_query():
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404, text=empty_sbazar())
        return httpx.Response(200, text=sbazar([sbazar_row()]))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        rows = CentralEuropeClient('sbazar', Settings(bazos_request_gap_seconds=0), client=client).fetch_new()
        assert len(requests) > 1
        assert len(rows) == 1
