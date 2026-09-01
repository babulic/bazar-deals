from decimal import Decimal
from pathlib import Path

import pytest

from bazar_deals.adapters.base import ListingSource
from bazar_deals.ai_identity import AIIdentity, AIIdentityClient, listing_key
from bazar_deals.config import Settings
from bazar_deals.domain import IdentifiedItem, Listing, Marketplace, Money, Vertical
from bazar_deals.identity import (
    ItemKind,
    ItemSpecs,
    extract_specs,
    identify,
    identity_subject,
    listing_text,
    similar_titles,
)
from bazar_deals.pipeline import hunt
from bazar_deals.soldcomps import SoldCompClient

ROOT = Path(__file__).parent / "fixtures"


class _Source(ListingSource):
    marketplace = Marketplace.BAZOS.value

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    def fetch_new(self, vertical: Vertical | None = None) -> list[Listing]:
        return self._listings


def listing(**overrides) -> Listing:
    payload = {
        "marketplace": Marketplace.BAZOS,
        "external_id": "1",
        "title": "Predám telefón",
        "description": "",
        "url": "https://mobil.bazos.sk/inzerat/1/",
        "price": Money(amount=Decimal("100"), currency="EUR"),
    }
    payload.update(overrides)
    return Listing(**payload)


def test_listing_text_reads_past_the_title() -> None:
    text = listing_text(
        listing(
            title="Apple iPhone 13",
            description="Kapacita 128 GB, batéria 89%.",
            raw={"shortDescription": "Midnight", "categoryPath": [{"name": "Mobily"}]},
        )
    )
    assert "128 GB" in text
    assert "Midnight" in text
    assert "Mobily" in text


def test_listing_text_reads_nested_ebay_item_specifics() -> None:
    text = listing_text(
        listing(
            title="iPhone 13",
            description="",
            raw={
                "detail": {
                    "localizedAspects": [
                        {"name": "Storage Capacity", "value": "128 GB"},
                        {"name": "Brand", "value": "Apple"},
                    ]
                }
            },
        )
    )
    assert "128 GB" in text
    assert "Apple" in text
    item = identify(
        listing(
            title="iPhone 13",
            description="",
            raw={
                "detail": {
                    "localizedAspects": [
                        {"name": "Storage Capacity", "value": "128 GB"},
                    ]
                }
            },
        )
    )
    assert "128gb" in item.search_query
    assert item.specs.storage == frozenset({"128gb"})


def test_capacity_hidden_in_the_body_still_reaches_the_search_query() -> None:
    item = identify(listing(title="Apple iPhone 13", description="Kapacita 128 GB, pekný stav."))
    assert item.kind == ItemKind.PHONES.value
    assert "128gb" in item.search_query
    assert item.specs.storage == frozenset({"128gb"})


def test_capacity_from_the_body_rejects_a_different_capacity_comp() -> None:
    ad = listing(title="Apple iPhone 13", description="Kapacita 128 GB, plne funkčný.")
    specs = extract_specs(listing_text(ad))
    # Titles alone look identical, which is exactly the trap.
    assert similar_titles("Apple iPhone 13", "Apple iPhone 13 256GB") is True
    assert (
        similar_titles("Apple iPhone 13", "Apple iPhone 13 256GB", left_specs=specs) is False
    )
    assert similar_titles("Apple iPhone 13", "Apple iPhone 13 128GB", left_specs=specs) is True


def test_production_year_separates_two_runs_of_the_same_chip() -> None:
    older = "Videochip CSG 8565 R2 VIC-II z roku 1991 pre Commodore C64C"
    newer = "Videochip CSG 8565 R2 VIC-II z roku 1993 pre Commodore C64C"
    assert similar_titles(newer, older) is False
    assert similar_titles(newer, "Commodore CSG 8565 R2 VIC-II 1993 C64C") is True


def test_split_part_number_is_recovered() -> None:
    specs = extract_specs("Videochip CSG 8565 R2 VIC-II pre Commodore C64C")
    assert "8565" in specs.model_codes
    assert "8565r2" in specs.model_codes
    assert extract_specs("Commodore MOS 6510 CBM procesor").model_codes == frozenset({"6510"})


def test_measurements_and_ratings_are_not_part_numbers() -> None:
    specs = extract_specs("Nový zdroj 220V, kábel 1.2m, hmotnosť 90g, veľkosť 16cm")
    assert specs.model_codes == frozenset()
    assert extract_specs("61g ružový chalcedón Banská Štiavnica").model_codes == frozenset()


def test_a_lot_is_not_priced_from_a_single_piece() -> None:
    assert (
        similar_titles(
            "8ks kovové kľučky na eurookná Winkhaus dural",
            "Kovové kľučky na eurookná Winkhaus dural",
        )
        is False
    )
    assert extract_specs("8ks kovové kľučky Winkhaus").lot_size == 8
    assert extract_specs("Kovové kľučky Winkhaus").lot_size is None
    lot = identify(listing(title="8ks kovové kľučky na eurookná Winkhaus dural"))
    assert "8ks" in lot.search_query


def test_vague_title_still_matches_sold_comps_from_the_body() -> None:
    ad = listing(
        title="Predám telefón",
        description="Apple iPhone 13, kapacita 128 GB, Midnight, plne funkčný.",
    )
    item = identify(ad)
    assert "iphone" in item.search_query
    assert "128gb" in item.search_query
    # The headline alone cannot match a real sold title.
    assert similar_titles(item.listing.title, "Apple iPhone 13 128GB Midnight") is False
    assert (
        similar_titles(
            identity_subject(item),
            "Apple iPhone 13 128GB Midnight",
            left_specs=item.specs,
            left_kind=ItemKind.PHONES,
        )
        is True
    )


def test_mineral_locality_from_inflected_body_is_required_of_comps() -> None:
    ad = listing(
        title="Galenit",
        description="Pekný vzorok z Banskej Štiavnice, 61g, zberateľský kus.",
    )
    item = identify(ad)
    assert any("stiavnic" in place for place in item.specs.localities)
    assert "stiavnica" in item.search_query
    assert (
        similar_titles(
            "Galenit kryštál Banská Štiavnica",
            "Galenit kryštál Namibia Goboboseb",
            left_specs=item.specs,
            left_kind=ItemKind.MINERALS,
        )
        is False
    )
    assert similar_titles(
        "Galenit kryštál Banská Štiavnica",
        "Galenit kryštál Schemnitz Banská Štiavnica",
        left_specs=item.specs,
        left_kind=ItemKind.MINERALS,
    )
    # A domestic seller location is not a specimen origin.
    assert extract_specs("Ametyst, osobný odber Slovensko").localities == frozenset()


def test_specs_conflict_is_asymmetric() -> None:
    bare = ItemSpecs()
    detailed = ItemSpecs(storage=frozenset({"128gb"}))
    # A comp that says more than the ad is still usable.
    assert bare.conflicts_with(detailed) is False
    # An ad that states a capacity may not be priced from a silent comp.
    assert detailed.conflicts_with(bare) is True


def test_variant_gate_still_applies_to_phones() -> None:
    assert similar_titles("Apple iPhone 13 Pro 128GB", "Apple iPhone 13 128GB") is False


def test_minerals_are_unaffected_by_the_new_gates() -> None:
    assert (
        similar_titles(
            "29mm kryštál Ametystu s kalcitom v ryolite, Namíbia",
            "Amethyst Kristall Stufe Namibia Goboboseb",
        )
        is False
    )
    assert similar_titles(
        "Ametyst kryštál Namíbia Goboboseb",
        "Ametyst kryštál Namíbia Goboboseb Brandberg",
    )


class _FakeReviewer:
    """Stands in for the Copilot transport."""

    def __init__(self, payload: str, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def complete(self, prompt: str) -> tuple[str, list[str], str]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Copilot CLI is not installed")
        assert "LISTING START" in prompt
        return self.payload, [], "copilot:auto"


PAYLOAD = """{
  "canonical_name": "Commodore CSG 8565R2 VIC-II",
  "kind": "hardware",
  "search_query": "commodore 8565r2 vic-ii",
  "storage": [],
  "model_codes": ["8565r2"],
  "years": ["1993"],
  "lot_size": 1,
  "confidence": 0.9,
  "reason": "The body names the chip and its production year."
}"""


def identity_client(tmp_path: Path, reviewer) -> AIIdentityClient:
    settings = Settings(ai_min_confidence=0.75, ai_review_ttl_days=14)
    return AIIdentityClient(settings, reviewer=reviewer, db_path=tmp_path / "ai.sqlite")


def test_ai_identity_reads_the_body_and_is_cached(tmp_path: Path) -> None:
    reviewer = _FakeReviewer(PAYLOAD)
    client = identity_client(tmp_path, reviewer)
    ad = listing(
        title="Predám čip do počítača",
        description="Je to videočip CSG 8565 R2 VIC-II z roku 1993 pre Commodore C64C.",
    )

    first = client.identify(ad)
    assert first is not None
    assert first.search_query == "commodore 8565r2 vic-ii"
    assert "8565r2" in first.specs.model_codes
    assert first.cached is False

    second = client.identify(ad)
    assert second is not None and second.cached is True
    # The cache means one advertisement costs one Copilot call, not two.
    assert reviewer.calls == 1


def test_ai_identity_declines_when_it_is_unsure(tmp_path: Path) -> None:
    unsure = PAYLOAD.replace('"confidence": 0.9', '"confidence": 0.2')
    assert identity_client(tmp_path, _FakeReviewer(unsure)).identify(listing()) is None

    empty = PAYLOAD.replace('"search_query": "commodore 8565r2 vic-ii"', '"search_query": ""')
    assert identity_client(tmp_path, _FakeReviewer(empty)).identify(listing()) is None


def test_ai_identity_never_carries_a_price(tmp_path: Path) -> None:
    identity = identity_client(tmp_path, _FakeReviewer(PAYLOAD)).identify(listing())
    assert identity is not None
    assert not hasattr(identity, "quick_sale_price_eur")
    assert set(AIIdentity.model_fields) & {"price", "value_eur"} == set()


def test_listing_key_is_stable_per_marketplace_listing() -> None:
    assert listing_key(listing(external_id="42")) == "bazos:42"


class _StubIdentifier:
    def __init__(self, result: IdentifiedItem | None, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls = 0

    def apply(self, ad: Listing, item: IdentifiedItem) -> IdentifiedItem | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Copilot unavailable")
        return self.result


def weak_listing() -> Listing:
    return listing(
        marketplace=Marketplace.BAZOS,
        external_id="weak",
        title="Predám",
        description="Ozvite sa.",
        url="https://pc.bazos.sk/inzerat/weak/",
        price=Money(amount=Decimal("38"), currency="EUR"),
    )


def test_weak_identity_is_dropped_without_an_identifier(capsys) -> None:
    hunt(_Source([weak_listing()]), sold=SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html"))
    funnel = capsys.readouterr().out
    assert "identity_weak=1" in funnel
    assert "identity_ai_rescued=0" in funnel


def test_ai_rescue_puts_a_named_item_back_in_the_funnel(capsys) -> None:
    ad = weak_listing()
    rescued = IdentifiedItem(
        listing=ad,
        vertical=None,
        canonical_name="Commodore 1541-II disk drive",
        model="commodore 1541 ii",
        search_query="commodore 1541 ii",
        kind="hardware",
        identified_by="copilot:auto",
        confidence=0.9,
    )
    stub = _StubIdentifier(rescued)
    deals = hunt(
        _Source([ad]),
        sold=SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html"),
        identifier=stub,
    )
    funnel = capsys.readouterr().out
    assert stub.calls == 1
    assert "identity_ai_rescued=1" in funnel
    assert "identity_weak=0" in funnel
    assert deals and deals[0].item.identified_by == "copilot:auto"


def test_failed_ai_rescue_is_counted_and_the_item_still_drops(capsys) -> None:
    stub = _StubIdentifier(None, fail=True)
    hunt(
        _Source([weak_listing()]),
        sold=SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html"),
        identifier=stub,
    )
    funnel = capsys.readouterr().out
    assert "identity_ai_failed=1" in funnel
    assert "identity_weak=1" in funnel


def test_ai_rescue_respects_its_budget(capsys) -> None:
    settings = Settings(ai_max_identifications=2)
    ads = [
        listing(
            external_id=f"weak-{index}",
            title="Predám",
            description="Ozvite sa.",
            url=f"https://pc.bazos.sk/inzerat/weak-{index}/",
            price=Money(amount=Decimal("38"), currency="EUR"),
        )
        for index in range(5)
    ]
    stub = _StubIdentifier(None)
    hunt(
        _Source(ads),
        settings=settings,
        sold=SoldCompClient(fixture_path=ROOT / "ebay_sold_1541.html"),
        identifier=stub,
    )
    capsys.readouterr()
    assert stub.calls == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("iPhone 13 128GB", frozenset({"128gb"})),
        ("Pamäť 512 GB", frozenset({"512gb"})),
        ("Disk 2 TB", frozenset({"2tb"})),
        ("Bez uvedenej kapacity", frozenset()),
    ],
)
def test_storage_extraction(text: str, expected: frozenset[str]) -> None:
    assert extract_specs(text).storage == expected


def test_selling_boilerplate_never_becomes_the_product_identity() -> None:
    from bazar_deals.identity import sold_query

    # Before boilerplate was stripped this produced the query "predam ozvite"
    # with generic confidence, so a junk ad looked identifiable.
    assert sold_query("Predám, ozvite sa. Cena dohodou, poštovné Packetou") is None
    item = identify(listing(title="Predám", description="Ozvite sa, cena dohodou."))
    assert item.search_query == ""
    assert item.confidence < 0.5


def test_boilerplate_stripping_keeps_the_actual_product_words() -> None:
    from bazar_deals.identity import sold_query

    query = sold_query("Predám Commodore 1541-II disk drive, cena dohodou, osobný odber")
    assert query is not None
    assert "commodore" in query
    assert "1541" in query
    assert "predam" not in query
    assert "dohodou" not in query


def test_listing_boilerplate_znacka_stav_nove_dropped_from_query() -> None:
    from bazar_deals.identity import sold_query

    query = sold_query("wlvs siltovka znacka nike stav nove")
    assert query is not None
    assert "siltovka" in query
    assert "znacka" not in query
    assert "stav" not in query
    assert "nove" not in query


def test_rss_image_markup_never_reaches_the_identity() -> None:
    from bazar_deals.identity import sold_query, strip_markup

    body = (
        '<img src="https://www.bazos.sk/img/1m/448/194978448.jpg" />'
        "Predám rôzne pamäte RAM Kingston o rôznych veľkostiach, cena dohodou"
    )
    cleaned = strip_markup(body)
    for debris in ("img", "src", "https", "www", "jpg", "194978448"):
        assert debris not in cleaned.lower()

    query = sold_query(listing_text(listing(title="Kingston elixir hynix", description=body)))
    assert query is not None
    assert "kingston" in query
    for debris in ("img", "src", "https", "www"):
        assert debris not in query


def test_strip_markup_leaves_ordinary_text_alone() -> None:
    from bazar_deals.identity import strip_markup

    assert strip_markup("Commodore 1541-II, 5 < 7 a 9 > 3") == "Commodore 1541-II, 5 < 7 a 9 > 3"
    assert strip_markup("") == ""
