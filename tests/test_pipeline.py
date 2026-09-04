from decimal import Decimal
from pathlib import Path

from bazar_deals.adapters.bazos import BazosRssClient, _split_price
from bazar_deals.catalog import is_bulky
from bazar_deals.cli import main
from bazar_deals.config import Settings
from bazar_deals.domain import AIReview, Action, Listing, Marketplace, Money
from bazar_deals.pipeline import hunt, hunt_sources, score_listings
from bazar_deals.soldcomps import SoldComp, SoldCompClient

FIXTURE = Path(__file__).parent / "fixtures" / "bazos_rss.xml"
SOLD = Path(__file__).parent / "fixtures" / "ebay_sold_1541.html"


def test_bazos_hunts_slovakia_and_czech_rss() -> None:
    assert BazosRssClient().sites == ("sk", "cz")


def test_rss_parses_price_from_title() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert listings[0].title == "Commodore 1541-II disk drive"
    assert listings[0].price.amount == 38


def test_current_bazos_rss_colon_price_is_parsed() -> None:
    amount, title = _split_price("Apple iPhone 13 128 GB: 1 099")
    assert title == "Apple iPhone 13 128 GB"
    assert amount == 1099


def test_unpriced_bazos_suffix_remains_zero() -> None:
    amount, title = _split_price("Apple iPhone 13: Dohodou")
    assert title == "Apple iPhone 13"
    assert amount == 0


def test_fixture_is_scored_but_not_buy_when_net_profit_is_under_20() -> None:
    deals = hunt(BazosRssClient(fixture_path=FIXTURE), sold=SoldCompClient(fixture_path=SOLD))
    cheap = [deal for deal in deals if deal.item.listing.price.amount == 38]
    assert cheap
    assert cheap[0].action is Action.SKIP
    assert cheap[0].costs.net_profit < 20
    assert cheap[0].item.canonical_name == "Commodore 1541-II disk drive"


def test_fixture_drops_bulky_couch() -> None:
    listings = BazosRssClient(fixture_path=FIXTURE).fetch_new()
    assert all("gauč" not in item.title.casefold() for item in listings)
    assert is_bulky("Starý gauč")


def test_cli_offline_reports_no_false_buy(capsys) -> None:
    assert main(["hunt", "--offline", "--source", "bazos"]) == 0
    out = capsys.readouterr().out
    assert "filter:" in out
    assert "No deals" in out
    assert "BUY: áno" not in out


def test_under_min_price_is_dropped() -> None:
    from bazar_deals.adapters.base import ListingSource
    from bazar_deals.domain import Listing, Marketplace, Money, Vertical

    class _Cheap(ListingSource):
        marketplace = Marketplace.BAZOS.value

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return [
                Listing(
                    marketplace=Marketplace.BAZOS,
                    external_id="lv",
                    title="LOUIS VUITTON BLACK EDITION",
                    url="https://oblecenie.bazos.sk/inzerat/lv/",
                    price=Money(amount=Decimal("0.15"), currency="EUR"),
                )
            ]

    deals = hunt(_Cheap(), sold=SoldCompClient(fixture_path=SOLD))
    assert deals == []


def test_hunt_batch_price_book_scores_without_ebay(tmp_path) -> None:
    from unittest.mock import patch

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Commodore 1541-II disk drive",
            description="Funkčná mechanika, krabica.",
            url=f"https://pc.bazos.sk/inzerat/1541-{index}/",
            price=Money(amount=Decimal(str(price)), currency="EUR"),
        )
        for index, price in enumerate((38, 80, 85, 90, 95, 100))
    ]
    settings = Settings(comps_db=str(tmp_path / "comps.sqlite"))
    sold = SoldCompClient(settings)
    with (
        patch.object(sold, "_bazos_search", return_value=[]),
        patch.object(sold, "_aukro_search", return_value=[]),
        patch.object(sold, "_vinted_search", return_value=[]),
    ):
        run = score_listings(listings, settings, sold)
    cheap = [deal for deal in run.deals if deal.item.listing.price.amount == 38]
    assert cheap
    assert cheap[0].action is Action.SKIP
    assert cheap[0].costs.net_profit < 30
    assert all(deal.action is not Action.BUY for deal in run.deals)
    assert cheap[0].item.sold_label.startswith("trhová rýchlopredajná cena")
    assert run.funnel["scored"] + run.funnel["above_typical"] == 6
    assert run.funnel["scored"] >= 1
    assert run.funnel["no_sold_comps"] == 0
    import sqlite3

    with sqlite3.connect(settings.comps_db) as conn:
        row = conn.execute("SELECT source, n FROM sold_queries").fetchone()
    assert row is not None
    assert row[0] == "market"
    assert int(row[1]) >= 5


def test_invalid_price_has_its_own_funnel_metric(capsys) -> None:
    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="zero",
        title="Apple iPhone 13 128 GB",
        url="https://mobil.bazos.sk/inzerat/zero/",
        price=Money(amount=0, currency="EUR"),
    )
    score_listings([listing], Settings(), SoldCompClient(fixture_path=SOLD))
    out = capsys.readouterr().out
    assert "not_buy_now=0" in out
    assert "invalid_price=1" in out


def test_asking_only_comp_can_reach_required_ai_gate() -> None:
    class _AskingComps:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="asking-only conservative P25 with haircut (n=8)",
                reliable_for_buy=False,
            )

    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("110"),
                confidence=0.9,
                reason="Exact model and conservative quick-sale value verified.",
                source_urls=["https://example.com/evidence"],
                model="copilot:auto",
            )

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="iphone",
        title="Apple iPhone 13 128GB",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
        url="https://mobil.bazos.sk/inzerat/iphone/",
        price=Money(amount=20, currency="EUR"),
    )
    settings = Settings(ai_review_enabled=True, ai_review_required=True, min_net_profit_eur=30)
    deals = score_listings([listing], settings, _AskingComps(), reviewer=_Reviewer()).deals
    assert len(deals) == 1
    assert deals[0].action is Action.BUY
    assert deals[0].ai_review is not None


def test_lookup_budget_counts_unique_queries(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_sold_lookups"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="sold P25 (n=8)",
                reliable_for_buy=True,
            )

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/{index}/",
            price=Money(amount=20 + index, currency="EUR"),
        )
        for index in range(2)
    ]
    deals = score_listings(listings, Settings(), _Sold()).deals
    assert len(deals) == 2


def test_score_listings_caps_detail_work(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 3
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/cap-{index}/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        )
        for index in range(10)
    ]
    run = score_listings(listings, Settings(), _Sold())
    assert run.funnel["usable"] == 10
    assert run.funnel["score_capped"] == 7
    assert run.funnel["scored"] == 3


def test_score_listings_caps_on_wall_clock(monkeypatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr("bazar_deals.pipeline.time.monotonic", lambda: clock["t"])

    class _Sold:
        def median_sold(self, listing, **kwargs):
            clock["t"] += 20
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/time-{index}/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        )
        for index in range(6)
    ]
    run = score_listings(listings, Settings(hunt_score_seconds=30), _Sold())
    assert run.funnel["usable"] == 6
    assert run.funnel["scored"] == 2
    assert run.funnel["score_capped"] == 4


def test_scheduled_score_budget_accepts_ninety_minutes():
    assert Settings(hunt_score_seconds=5400).hunt_score_seconds == 5400


def test_unconfirmed_sbazar_does_not_fill_the_score_cap(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 2
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=f"bazos-{index}",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/ready-{index}/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        )
        for index in range(2)
    ] + [
        Listing(
            marketplace=Marketplace.SBAZAR,
            external_id=f"sbazar-{index}",
            title="Nintendo Switch V2",
            url=f"https://www.sbazar.cz/inzerat/{index}-switch",
            price=Money(amount=Decimal("40"), currency="EUR"),
            ships_to_slovakia=None,
        )
        for index in range(4)
    ]
    run = score_listings(listings, Settings(), _Sold())
    assert run.funnel["usable"] == 6
    assert run.funnel["score_capped"] == 4
    assert run.funnel["scored"] == 2
    assert {deal.item.listing.marketplace for deal in run.deals} == {Marketplace.BAZOS}


def test_overpriced_listing_is_not_scored() -> None:
    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("7.28"),
                sample=17,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=17)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    listing = Listing(
        marketplace=Marketplace.VINTED,
        external_id="dear-phone",
        title="Apple iPhone 13 128GB",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
        url="https://www.vinted.sk/items/dear-iphone-13",
        price=Money(amount=Decimal("90"), currency="EUR"),
    )
    run = score_listings([listing], Settings(), _Sold())
    assert run.deals == []
    assert run.funnel["scored"] == 0
    assert run.funnel["below_net_profit"] == 0
    assert run.funnel["above_typical"] == 1
    assert run.source_stats[Marketplace.VINTED]["scored"] == 0


def test_cached_overpriced_does_not_consume_score_cap(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def cached_typical(self, listing, **kwargs):
            if listing.external_id == "over":
                return SoldComp(
                    median=Decimal("7.28"),
                    sample=17,
                    label="cached P25×0.75 (n=17)",
                    reliable_for_buy=True,
                )
            return None

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="over",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/over/",
            price=Money(amount=Decimal("20"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="under",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/under/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(), sold)
    assert sold.live == ["under"]
    assert run.funnel["above_typical"] == 1
    assert run.funnel["scored"] == 1
    assert run.funnel["score_capped"] == 0
    assert [deal.item.listing.external_id for deal in run.deals] == ["under"]


def test_no_sold_comps_do_not_consume_score_cap(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def cached_typical(self, listing, **kwargs):
            return None

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            if listing.external_id == "miss":
                return None
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="miss",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/miss/",
            price=Money(amount=Decimal("30"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="hit",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/hit/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(), sold)
    assert sold.live == ["miss", "hit"]
    assert run.funnel["no_sold_comps"] == 1
    assert run.funnel["scored"] == 1
    assert run.funnel["score_capped"] == 0
    assert [deal.item.listing.external_id for deal in run.deals] == ["hit"]


def test_cheaper_hunt_target_is_scored_first_when_cap_is_one(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def cached_typical(self, listing, **kwargs):
            return None

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="dear",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/dear/",
            price=Money(amount=Decimal("90"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="cheap",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/cheap/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(), sold)
    assert sold.live == ["cheap"]
    assert [deal.item.listing.external_id for deal in run.deals] == ["cheap"]
    assert run.funnel["score_capped"] == 1


def test_iphone_is_scored_before_cheaper_c64_game_when_cap_is_one(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def cached_typical(self, listing, **kwargs):
            return None

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

        def prepare_price_book(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="game",
            title="Computing Videothek Billardspiele Commodore 64/128",
            description="Kassette s jednou hrou na C64.",
            url="https://pc.bazos.sk/inzerat/game/",
            price=Money(amount=Decimal("24"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="phone",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/phone/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(), sold)
    assert sold.live == ["phone"]
    assert [deal.item.listing.external_id for deal in run.deals] == ["phone"]
    assert run.funnel["drop_kind"] == 1
    assert run.funnel["score_capped"] == 0


def test_cached_buy_candidate_is_scored_without_live_lookup(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def cached_typical(self, listing, **kwargs):
            if listing.external_id == "deal":
                return SoldComp(
                    median=Decimal("120"),
                    sample=8,
                    label="cached P25×0.75 (n=8)",
                    reliable_for_buy=True,
                )
            return None

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.VINTED,
            external_id="noise",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://www.vinted.sk/items/noise",
            price=Money(amount=Decimal("90"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="deal",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/deal/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(ai_review_enabled=False), sold)
    assert "deal" not in sold.live
    cheap = [deal for deal in run.deals if deal.item.listing.external_id == "deal"]
    assert cheap
    assert cheap[0].action is Action.BUY


def test_unbranded_clothing_does_not_consume_score_cap(monkeypatch) -> None:
    from copy import deepcopy

    import bazar_deals.pipeline as pipeline

    configured = deepcopy(pipeline.rules())
    configured["hunt"]["max_score_listings"] = 1
    monkeypatch.setattr(pipeline, "rules", lambda: configured)

    class _Sold:
        live: list[str] = []

        def median_sold(self, listing, **kwargs):
            self.live.append(listing.external_id)
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    sold = _Sold()
    listings = [
        Listing(
            marketplace=Marketplace.VINTED,
            external_id="cap",
            title="wlvs siltovka",
            description="Dámska šiltovka, nová, s visačkou.",
            url="https://www.vinted.sk/items/cap-wlvs-siltovka",
            price=Money(amount=Decimal("20"), currency="EUR"),
        ),
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id="phone",
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url="https://mobil.bazos.sk/inzerat/phone/",
            price=Money(amount=Decimal("40"), currency="EUR"),
        ),
    ]
    run = score_listings(listings, Settings(), sold)
    assert "cap" not in sold.live
    assert sold.live == ["phone"]
    assert run.funnel["skip_keyword"] == 1
    assert run.funnel["usable"] == 1
    assert run.funnel["scored"] == 1


def test_long_description_skips_detail_http() -> None:
    class _Enricher:
        marketplace = Marketplace.BAZOS.value

        def enrich_listing(self, listing):
            raise AssertionError("catalog text is enough; do not fetch the ad page")

        def fetch_new(self, vertical=None):
            return []

    class _Sold:
        def median_sold(self, listing, **kwargs):
            return SoldComp(
                median=Decimal("120"),
                sample=8,
                label="trhová rýchlopredajná cena, P25×0.75 bazos/aukro/vinted (n=8)",
                reliable_for_buy=True,
            )

        def seed_asking(self, listings):
            return None

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="body",
        title="Apple iPhone 13 128GB",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia, krabica.",
        url="https://mobil.bazos.sk/inzerat/body/",
        price=Money(amount=Decimal("40"), currency="EUR"),
    )
    run = score_listings(
        [listing],
        Settings(),
        _Sold(),
        enrichers={Marketplace.BAZOS: _Enricher()},
    )
    assert run.funnel["scored"] == 1


def test_price_book_from_hunt_batch_scores_and_can_buy(tmp_path) -> None:
    from unittest.mock import patch

    listings = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=str(index),
            title="Apple iPhone 13 128GB",
            description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
            url=f"https://mobil.bazos.sk/inzerat/iphone-{index}/",
            price=Money(amount=Decimal("20") if index == 0 else Decimal("110"), currency="EUR"),
        )
        for index in range(6)
    ]

    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("110"),
                confidence=0.9,
                reason="Exact model verified.",
                source_urls=["https://example.com/evidence"],
                model="copilot:auto",
            )

    settings = Settings(
        comps_db=str(tmp_path / "comps.sqlite"),
        ai_review_enabled=True,
        ai_review_required=True,
        min_net_profit_eur=30,
    )
    sold = SoldCompClient(settings)
    with (
        patch.object(sold, "_bazos_search", return_value=[]),
        patch.object(sold, "_aukro_search", return_value=[]),
        patch.object(sold, "_vinted_search", return_value=[]),
    ):
        run = score_listings(listings, settings, sold, reviewer=_Reviewer())
    assert run.funnel["scored"] >= 1
    assert run.funnel["scored"] + run.funnel["above_typical"] == 6
    assert run.funnel["asking_only_comps"] == 0
    assert run.funnel["no_sold_comps"] == 0
    assert any(deal.action is Action.BUY for deal in run.deals)
    cheap = [deal for deal in run.deals if deal.item.listing.external_id == "0"][0]
    assert cheap.action is Action.BUY
    assert cheap.costs.net_profit >= 30
    assert cheap.item.sold_label.startswith("trhová rýchlopredajná cena")


def test_live_market_comps_can_buy_when_hunt_batch_cannot(tmp_path) -> None:
    from unittest.mock import patch

    cheap = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=f"cheap-{index}",
            title="Commodore 1541-II disk drive",
            description="Funkčná mechanika, krabica.",
            url=f"https://pc.bazos.sk/inzerat/cheap-{index}/",
            price=Money(amount=Decimal("50"), currency="EUR"),
        )
        for index in range(6)
    ]
    live = [
        Listing(
            marketplace=Marketplace.BAZOS,
            external_id=f"live-{index}",
            title="Commodore 1541-II disk drive",
            description="Funkčná mechanika, krabica.",
            url=f"https://pc.bazos.sk/inzerat/live-{index}/",
            price=Money(amount=Decimal("160"), currency="EUR"),
        )
        for index in range(6)
    ]
    candidate = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="deal",
        title="Commodore 1541-II disk drive",
        description="Funkčná mechanika, krabica.",
        url="https://pc.bazos.sk/inzerat/deal/",
        price=Money(amount=Decimal("40"), currency="EUR"),
    )
    settings = Settings(comps_db=str(tmp_path / "comps.sqlite"), ai_review_enabled=False)
    sold = SoldCompClient(settings)
    with (
        patch.object(sold, "_bazos_search", return_value=live),
        patch.object(sold, "_aukro_search", return_value=[]),
        patch.object(sold, "_vinted_search", return_value=[]),
    ):
        run = score_listings([candidate, *cheap], settings, sold)
    deal = [row for row in run.deals if row.item.listing.external_id == "deal"]
    assert deal
    assert deal[0].action is Action.BUY
    assert deal[0].costs.net_profit >= 30
    assert deal[0].costs.estimated_resale == Decimal("120.00")


def test_hunt_sources_appends_sold_comp_notes() -> None:
    from bazar_deals.adapters.base import ListingSource
    from bazar_deals.domain import Vertical

    class _Empty(ListingSource):
        marketplace = Marketplace.BAZOS.value

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return []

    class _Sold:
        notes = ["price book: Bazos/Aukro/Vinted/eBay P25×0.75 stored in comps DB and reused"]

        def median_sold(self, listing, **kwargs):
            return None

    run = hunt_sources([_Empty()], settings=Settings(), sold=_Sold())
    assert "bazos: fetched 0" in run.fetch_notes
    assert not any(note.startswith("price book:") for note in run.fetch_notes)


def test_hunt_sources_drops_delivery_and_access_notes() -> None:
    from bazar_deals.adapters.base import ListingSource
    from bazar_deals.domain import Vertical

    class _Sbazar(ListingSource):
        marketplace = Marketplace.SBAZAR.value
        notes = [
            "sbazar: READY: 329 readable offers (SK eligibility checked separately)",
            "sbazar: NEEDS_DELIVERY_CONFIRMATION: 329 offers require detail or manual evidence",
        ]

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return []

    class _Facebook(ListingSource):
        marketplace = "facebook"
        notes = [
            "facebook: LOGIN_REQUIRED: manual import only; browser login is not unattended API access",
        ]

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return []

    class _Sold:
        notes = ["price book: live query budget exhausted (16); remaining products are unvalued"]

        def median_sold(self, listing, **kwargs):
            return None

    run = hunt_sources([_Sbazar(), _Facebook()], settings=Settings(), sold=_Sold())
    assert "sbazar: fetched 0" in run.fetch_notes
    assert "facebook: fetched 0" not in run.fetch_notes
    assert not any("NEEDS_DELIVERY_CONFIRMATION" in note for note in run.fetch_notes)
    assert not any(": READY:" in note for note in run.fetch_notes)
    assert not any("LOGIN_REQUIRED" in note for note in run.fetch_notes)
    assert not any("live query budget exhausted" in note for note in run.fetch_notes)


def test_hunt_sources_fetches_ebay() -> None:
    from bazar_deals.adapters.base import ListingSource
    from bazar_deals.domain import Vertical

    class _Ebay(ListingSource):
        marketplace = Marketplace.EBAY.value

        def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
            return [
                Listing(
                    marketplace=Marketplace.EBAY,
                    external_id="itm",
                    title="Apple iPhone 13 128GB",
                    url="https://www.ebay.de/itm/itm",
                    price=Money(amount=Decimal("40"), currency="EUR"),
                    ships_to_slovakia=True,
                    buy_now=True,
                )
            ]

    class _Sold:
        notes: list[str] = []

        def median_sold(self, listing, **kwargs):
            return None

        def seed_asking(self, listings):
            return None

    settings = Settings(ebay_client_id="app-id", ebay_client_secret="cert-id", ebay_retention_enabled=True)
    run = hunt_sources([_Ebay()], settings=settings, sold=_Sold())
    assert "ebay: fetched 1" in run.fetch_notes
    assert run.funnel["fetched"] == 1
