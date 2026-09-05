import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from bazar_deals.adapters.central_europe import CentralEuropeClient
from bazar_deals.cli import main
from bazar_deals.config import Settings
from bazar_deals.domain import Action, IdentifiedItem
from bazar_deals.manual_import import load_manual_offers
from bazar_deals.pipeline import _to_eur
from bazar_deals.scoring import score_deal
from bazar_deals.selling.demand import find_buyers
from bazar_deals.selling.inventory import Inventory, InventoryItem
from bazar_deals.soldcomps import SoldCompClient


def row(**updates):
    return dict(marketplace='olx', external_id='123', title='Nintendo Switch V2',
                description='Working console with charger, selected manually.',
                url='https://www.olx.pl/d/oferta/switch-123.html', price='215', currency='PLN',
                available=True, checked_at=datetime.now(timezone.utc).isoformat(),
                fulfillment='delivery_sk', fulfillment_cost='43', fulfillment_currency='PLN',
                evidence='Seller confirmed shipping to Bratislava at the stated price.', **updates)


def load(tmp_path, **updates):
    payload = row()
    payload.update(updates)
    path = tmp_path / 'offers.json'
    path.write_text(json.dumps([payload]))
    return load_manual_offers(path)[0]


def deal(listing):
    listing = _to_eur(listing, Decimal('25'), Decimal('4.3'))
    item = IdentifiedItem(listing=listing, vertical=None, canonical_name='Nintendo Switch V2', confidence=1)
    return score_deal(item, Decimal('200'), listing.shipping_cost.amount if listing.shipping_cost else None, settings=Settings())


@pytest.mark.parametrize('method', ['delivery_sk', 'pickup_sk'])
def test_fresh_manual_delivery_or_sk_pickup_reaches_scoring(tmp_path, method):
    listing = load(tmp_path, fulfillment=method)
    scored = deal(listing)
    assert scored.action == Action.BUY
    assert scored.costs.buy_price == Decimal('50')
    assert scored.costs.shipping == Decimal('10')
    assert scored.costs.fx_fee_reserve == Decimal('1.20')
    assert scored.costs.fees == Decimal('21.20')


@pytest.mark.parametrize('updates', [
    {'checked_at': (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()},
    {'checked_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    {'fulfillment': 'unknown'}, {'fulfillment_cost': None}, {'available': False},
    {'kind': 'wanted'}, {'title': 'Kupię Nintendo Switch V2'},
])
def test_incomplete_stale_or_unavailable_import_cannot_buy(tmp_path, updates):
    assert deal(load(tmp_path, **updates)).action == Action.SKIP


@pytest.mark.parametrize('updates', [
    {'price': '-1'}, {'price': 'NaN'}, {'fulfillment_cost': '-1'},
    {'currency': 'USD'}, {'url': 'https://olx.pl.evil.example/item'},
    {'url': 'http://www.olx.pl/item'}, {'url': 'https://user:pass@www.olx.pl/item'},
    {'checked_at': '2026-08-31T10:00:00'}, {'fulfillment': 'pickup_sk', 'evidence': ''},
    {'available': 'maybe'}, {'accidental_field': 'value'},
])
def test_invalid_import_rejected(tmp_path, updates):
    with pytest.raises(ValueError, match='row 1'):
        load(tmp_path, **updates)


def test_manual_evidence_not_overwritten_by_network_enrichment(tmp_path):
    def fail(request):
        pytest.fail('Manual evidence must not trigger a public detail fetch')
    listing = load(tmp_path)
    client = CentralEuropeClient('olx', Settings(), client=httpx.Client(transport=httpx.MockTransport(fail)))
    assert client.enrich_listing(listing) == listing


def test_manual_demands_only_and_offline_never_fetches(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Offline buyer search must not use the network')
    monkeypatch.setattr(httpx, 'get', fail)
    demand = load(tmp_path, kind='wanted', title='Kupię Nintendo Switch V2')
    offer = load(tmp_path, external_id='456')
    stock = Inventory(items=[InventoryItem(id='switch', segment='retro', title='Nintendo Switch V2', part_numbers=['Switch'])])
    digest = find_buyers(stock, Settings(eur_pln=Decimal('4.3')), manual_listings=[demand, offer], offline=True)
    assert len(digest.matches) == 1
    assert digest.matches[0].want.offer_eur == Decimal('49')


def test_comp_foreign_proceeds_reduced_once_even_after_pipeline_conversion(tmp_path):
    listing = _to_eur(load(tmp_path), Decimal('25'), Decimal('4.3'))
    book = SoldCompClient(Settings(comps_db=str(tmp_path / 'comps.sqlite')))
    comp = book._to_eur(listing)
    assert comp.price.amount == Decimal('49')
    assert book._to_eur(comp).price.amount == Decimal('49')
    assert listing.price.amount == Decimal('50')


def test_import_command_csv_and_no_source_overwrite(tmp_path):
    import csv
    path = tmp_path / 'input.csv'
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    out = tmp_path / 'normalized.json'
    assert main(['import', '--manual-in', str(path), '--listings-out', str(out)]) == 0
    assert json.loads(out.read_text())[0]['manual_import'] is True
    with pytest.raises(SystemExit):
        main(['import', '--manual-in', str(path), '--listings-out', str(path)])


@pytest.mark.parametrize('source,status', [('allegro_pl','ACCESS_NOT_GRANTED'), ('allegro_sk','ACCESS_NOT_GRANTED')])
def test_scheduled_manual_sources_do_not_attempt_blocked_requests(source, status):
    def fail(request):
        pytest.fail('A manual source must not make unattended requests')
    client = CentralEuropeClient(source, Settings(), client=httpx.Client(transport=httpx.MockTransport(fail)))
    assert client.fetch_new() == []
    assert status in client.notes[0]


def test_facebook_hunt_tries_public_html_and_fails_closed_on_login():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://www.facebook.com/login"})
    client = CentralEuropeClient(
        "facebook",
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.fetch_new() == []
    assert any("LOGIN_REQUIRED" in note for note in client.notes)


def _ddg_html(title: str, target: str, snippet: str = "") -> str:
    from urllib.parse import quote

    href = f"//duckduckgo.com/l/?uddg={quote(target, safe='')}&amp;rut=x"
    return (
        "<html>"
        f'<a rel="nofollow" class="result__a" href="{href}">{title}</a>'
        f'<a class="result__snippet" href="{href}">{snippet}</a>'
        "</html>"
    )


def test_facebook_hunt_reads_public_item_urls_from_search_index():
    def handler(request):
        url = str(request.url)
        if "duckduckgo.com" in url:
            return httpx.Response(
                200,
                text=_ddg_html(
                    "Apple iPhone SE 64GB",
                    "https://www.facebook.com/marketplace/item/555001122/",
                    "Predám iPhone SE",
                ),
            )
        return httpx.Response(302, headers={"Location": "https://www.facebook.com/login"})

    client = CentralEuropeClient(
        "facebook",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rows = client.search("iphone se")
    assert rows
    assert rows[0].external_id == "555001122"
    assert str(rows[0].url) == "https://www.facebook.com/marketplace/item/555001122/"
    assert rows[0].raw.get("indexed") is True
    assert any("via search index" in note for note in client.notes)


def test_olx_hunt_reads_public_oferta_urls_from_search_index():
    def handler(request):
        url = str(request.url)
        if "duckduckgo.com" in url:
            return httpx.Response(
                200,
                text=_ddg_html(
                    "Nintendo Switch OLED",
                    "https://www.olx.pl/d/oferta/nintendo-switch-oled-CID123.html",
                    "Sprzedam konsole",
                ),
            )
        return httpx.Response(403, text="ERROR: The request could")

    client = CentralEuropeClient(
        "olx",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rows = client.search("nintendo switch")
    assert rows
    assert "nintendo-switch-oled-CID123" in rows[0].external_id
    assert rows[0].raw.get("indexed") is True
    assert any("via search index" in note for note in client.notes)


def test_olx_hunt_reads_public_jsonld_and_fails_closed_on_login():
    from bazar_deals.adapters.central_europe import parse_public_listings

    def login(request):
        return httpx.Response(302, headers={"Location": "https://www.olx.pl/login"})

    blocked = CentralEuropeClient(
        "olx",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(login)),
    )
    assert blocked.fetch_new() == []
    assert any("LOGIN_REQUIRED" in note for note in blocked.notes)

    body = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Nintendo Switch V2",'
        '"description":"Working console. Shipping to Slovakia.",'
        '"image":"https://www.olx.pl/img/switch.jpg",'
        '"offers":{"@type":"Offer","url":"https://www.olx.pl/item/123",'
        '"price":"215","priceCurrency":"PLN",'
        '"availability":"https://schema.org/InStock",'
        '"shippingDetails":{"shippingDestination":{"addressCountry":"SK"},'
        '"shippingRate":{"value":"43","currency":"PLN"}}}}'
        "</script>"
    )
    parsed = parse_public_listings(body, "olx")
    assert parsed and parsed[0].ships_to_slovakia is True
    assert parsed[0].raw.get("images") == ["https://www.olx.pl/img/switch.jpg"]

    def offers(request):
        return httpx.Response(200, text=body)

    ready = CentralEuropeClient(
        "olx",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(offers)),
    )
    rows = ready.fetch_new()
    assert rows
    assert rows[0].marketplace.value == "olx"


def test_olx_empty_search_is_not_a_login_wall():
    empty = (
        "<html>Nie znaleziono ogłoszeń"
        '<script type="application/ld+json">'
        '{"@type":"ItemList","numberOfItems":0,"itemListElement":[]}'
        "</script></html>"
    )

    def handler(request):
        return httpx.Response(200, text=empty)

    client = CentralEuropeClient(
        "olx",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.search("nintendo") == []
    assert any("READY: no matches" in note for note in client.notes)


def test_olx_same_host_redirect_is_followed():
    body = (
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Nintendo Switch V2",'
        '"description":"Working console. Shipping to Slovakia.",'
        '"offers":{"@type":"Offer","url":"https://www.olx.pl/item/123",'
        '"price":"215","priceCurrency":"PLN",'
        '"availability":"https://schema.org/InStock"}}'
        "</script>"
    )

    def handler(request):
        if str(request.url).endswith("/q-nintendo/"):
            return httpx.Response(
                301,
                headers={"Location": "https://www.olx.pl/oferty/q-nintendo/?page=1"},
            )
        return httpx.Response(200, text=body)

    client = CentralEuropeClient(
        "olx",
        Settings(bazos_request_gap_seconds=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rows = client.search("nintendo")
    assert len(rows) == 1
    assert rows[0].external_id == "123"


def test_czk_reserve_for_goods_and_postage_matches_pln(tmp_path):
    listing = load(tmp_path, currency='CZK', price='1250', fulfillment_cost='250', fulfillment_currency='CZK')
    assert deal(listing).costs.fx_fee_reserve == Decimal('1.20')
    eur = load(tmp_path, currency='EUR', price='50', fulfillment_cost='10', fulfillment_currency='EUR')
    assert deal(eur).costs.fx_fee_reserve == 0
    settings = Settings(fx_fee_rate=Decimal('0.03'))
    converted = _to_eur(listing, Decimal('25'))
    item = IdentifiedItem(listing=converted, vertical=None, canonical_name='Nintendo Switch', confidence=1)
    assert score_deal(item, Decimal('200'), Decimal('10'), settings=settings).costs.fx_fee_reserve == Decimal('1.80')


def test_stale_import_cannot_seed_price_book(tmp_path):
    book = SoldCompClient(Settings(comps_db=str(tmp_path / 'comps.sqlite'), eur_pln=Decimal('4.3')))
    stale = load(tmp_path, checked_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    book.seed_asking([stale])
    assert book._asking_catalog == []


def test_regular_hunt_does_not_delete_comments():
    from pathlib import Path
    import yaml
    workflow = yaml.safe_load(Path('.github/workflows/hunt.yml').read_text())
    assert set(workflow['jobs']) == {'hunt'}
    assert 'schedule' not in workflow[True]
    assert workflow['permissions']['actions'] == 'write'
    assert workflow['jobs']['hunt']['outputs']['batch_complete']
    assert workflow['jobs']['hunt']['outputs']['dispatch_next']
    hunt_yaml = Path('.github/workflows/hunt.yml').read_text()
    assert '--batch-url "$HUNT_BATCH_URL"' in hunt_yaml
    assert 'actions/cache/save@v4' in hunt_yaml
    assert '/actions/workflows/hunt.yml/dispatches' in hunt_yaml
    assert 'ref: main' in hunt_yaml
    assert '0 */2 * * *' not in hunt_yaml
    assert 'HUNT_SCORE_SECONDS' not in hunt_yaml
    assert '--listings-in .cache/hunt-ebay.json' in hunt_yaml
    assert "delete-issue-comments" not in str(workflow)
    assert '--source olx' in hunt_yaml
    assert 'hunt-olx.json' in hunt_yaml
    assert '--source ebay' in hunt_yaml
    deploy_yaml = Path('.github/workflows/deploy-ebay-store.yml').read_text()
    assert 'CONDENS_SSH_PRIVATE_KEY' in deploy_yaml
    assert 'git archive --format=tar HEAD' in deploy_yaml
    assert 'tar -xf - -C /opt/bazar-deals' in deploy_yaml
    assert 'git pull' not in deploy_yaml
    assert 'docker compose -f deploy/ebay-store/compose.yml up -d --build' in deploy_yaml
    assert 'caddy reload --config /etc/caddy/Caddyfile' in deploy_yaml
    assert 'https://46-102-157-230.sslip.io/health' in deploy_yaml
    sell = yaml.safe_load(Path('.github/workflows/sell.yml').read_text())
    assert set(sell['jobs']) == {'sell-buyers', 'research'}
    assert "buyers == '0'" in str(sell['jobs']['research']['if'])
    assert "looped != '1'" in str(sell['jobs']['research']['if'])
    assert sell['jobs']['sell-buyers']['outputs']['looped']
    assert '--research' in str(sell['jobs']['research'])
