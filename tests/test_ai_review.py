import json
from collections import Counter
from decimal import Decimal

import httpx

from bazar_deals.ai_review import AIReviewClient
from bazar_deals.config import Settings
from bazar_deals.domain import AIReview, Condition, IdentifiedItem, Listing, Marketplace, Money
from bazar_deals.pipeline import _apply_ai_gate, _round_robin_listings
from bazar_deals.scoring import score_deal


def _listing(marketplace: Marketplace = Marketplace.BAZOS, external_id: str = "1") -> Listing:
    return Listing(
        marketplace=marketplace,
        external_id=external_id,
        title="Apple iPhone 13 128GB Midnight",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
        url=f"https://example.com/{marketplace.value}/{external_id}",
        price=Money(amount=Decimal("38"), currency="EUR"),
        condition=Condition.USED,
        ships_to_slovakia=True if marketplace is Marketplace.EBAY else None,
    )


def _deal():
    listing = _listing()
    item = IdentifiedItem(
        listing=listing,
        vertical=None,
        canonical_name="Apple iPhone 13 128GB",
        model="iphone 13 128gb",
        search_query="iphone 13 128gb",
        asking_sample=9,
        kind="phones",
        sold_label="konzervatívna rýchlopredajná cena, ebay.de sold P25 (n=9)",
        confidence=0.9,
    )
    return score_deal(item, Decimal("120"), Decimal("8"))


def test_ai_review_web_result_is_persisted_and_reused(tmp_path) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        payload = {
            "approved": True,
            "complete_product": True,
            "canonical_name": "Apple iPhone 13 128GB",
            "kind": "phones",
            "quick_sale_price_eur": 105,
            "confidence": 0.91,
            "reason": "Exact model and capacity verified from current resale evidence.",
            "source_urls": [],
        }
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(payload),
                                "annotations": [
                                    {"type": "url_citation", "url": "https://www.ebay.de/example-sold"}
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    settings = Settings(
        openai_api_key="test-key",
        openai_model="gpt-5.6-terra",
        ai_review_enabled=True,
        ai_review_required=True,
        comps_db=str(tmp_path / "comps.sqlite"),
    )
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        reviewer = AIReviewClient(settings, client=client)
        first = reviewer.review(_deal())
        second = reviewer.review(_deal())

    assert first.approved is True
    assert first.quick_sale_price_eur == Decimal("105.00")
    assert first.source_urls == ["https://www.ebay.de/example-sold"]
    assert first.cached is False
    assert second.cached is True
    assert calls["count"] == 1


def test_ai_review_cannot_approve_without_web_price_evidence(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "approved": True,
                                        "complete_product": True,
                                        "canonical_name": "Apple iPhone 13 128GB",
                                        "kind": "phones",
                                        "quick_sale_price_eur": 110,
                                        "confidence": 0.95,
                                        "reason": "No sources supplied.",
                                        "source_urls": [],
                                    }
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ]
            },
        )

    settings = Settings(
        openai_api_key="test-key",
        ai_review_enabled=True,
        ai_review_required=True,
        comps_db=str(tmp_path / "comps.sqlite"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        review = AIReviewClient(settings, client=client).review(_deal())
    assert review.approved is False


def test_ai_can_only_lower_price_and_veto_after_recalculation() -> None:
    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("80"),
                confidence=0.95,
                reason="Web evidence supports only 80 EUR quick-sale value.",
                source_urls=["https://www.ebay.de/example"],
                model="gpt-5.6-terra",
            )

    deal = _deal()
    assert deal.costs.estimated_resale == Decimal("120")
    settings = Settings(
        ai_review_enabled=True,
        ai_review_required=True,
        openai_api_key="test-key",
        min_net_profit_eur=Decimal("30"),
    )
    result = _apply_ai_gate([deal], settings, _Reviewer(), Counter())[0]
    assert result.costs.estimated_resale == Decimal("80")
    assert result.action.value == "skip"
    assert result.ai_review is not None


def test_round_robin_prevents_bazos_from_consuming_lookup_budget() -> None:
    listings = [
        *[_listing(Marketplace.BAZOS, f"b{i}") for i in range(6)],
        _listing(Marketplace.VINTED, "v1"),
        _listing(Marketplace.AUKRO, "a1"),
        _listing(Marketplace.EBAY, "e1"),
    ]
    ordered = _round_robin_listings(listings)
    assert [item.marketplace for item in ordered[:4]] == [
        Marketplace.VINTED,
        Marketplace.AUKRO,
        Marketplace.EBAY,
        Marketplace.BAZOS,
    ]
