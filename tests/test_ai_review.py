import json
from collections import Counter
from decimal import Decimal

import httpx

from bazar_deals.ai_review import AIReviewClient
from bazar_deals.config import Settings
from bazar_deals.domain import Action, AIReview, Condition, IdentifiedItem, Listing, Marketplace, Money
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


def test_ai_also_corrects_positive_near_miss_before_it_can_be_reported() -> None:
    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Spigen Apple Watch strap",
                kind="accessories",
                quick_sale_price_eur=Decimal("8"),
                confidence=0.95,
                reason="Same-product web prices are about 8 EUR.",
                source_urls=["https://example.test/strap"],
                model="copilot:auto",
            )

    near = _deal().model_copy(
        update={
            "action": Action.SKIP,
            "reason": "expected net profit 16 EUR < 20 EUR",
        }
    )
    result = _apply_ai_gate(
        [near],
        Settings(ai_review_enabled=True, ai_review_required=True),
        _Reviewer(),
        Counter(),
    )[0]
    assert result.costs.estimated_resale == Decimal("8")
    assert result.costs.net_profit < 0
    assert result.ai_review is not None


def test_ai_gate_retries_once_when_review_raises() -> None:
    class _Reviewer:
        def __init__(self) -> None:
            self.calls = 0

        def review(self, deal):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("copilot busy")
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("120"),
                confidence=0.95,
                reason="Verified from sold comps.",
                source_urls=["https://www.ebay.de/example"],
                model="copilot",
            )

    reviewer = _Reviewer()
    funnel = Counter()
    settings = Settings(
        ai_review_enabled=True,
        ai_review_required=True,
        openai_api_key="test-key",
        min_net_profit_eur=Decimal("30"),
    )
    result = _apply_ai_gate([_deal()], settings, reviewer, funnel)[0]
    assert reviewer.calls == 2
    assert funnel["ai_unavailable"] == 0
    assert result.action.value == "buy"


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


def test_ai_review_prompt_includes_body_specs_and_marketplace_fields(tmp_path) -> None:
    from bazar_deals.identity import ItemSpecs

    listing = Listing(
        marketplace=Marketplace.BAZOS,
        external_id="body",
        title="Predám telefón",
        description="Apple iPhone 13, kapacita 128 GB, Midnight.",
        url="https://mobil.bazos.sk/inzerat/body/",
        price=Money(amount=Decimal("38"), currency="EUR"),
        raw={"brand": "Apple", "shortDescription": "iPhone 13 128GB"},
    )
    item = IdentifiedItem(
        listing=listing,
        vertical=None,
        canonical_name="iphone 13 128gb",
        model="iphone 13 128gb",
        search_query="iphone 13 128gb",
        asking_sample=9,
        kind="phones",
        sold_label="konzervatívna rýchlopredajná cena, ebay.de sold P25 (n=9)",
        confidence=0.9,
        specs=ItemSpecs(storage=frozenset({"128gb"}), phone="iphone13"),
    )
    deal = score_deal(item, Decimal("120"), Decimal("8"))
    prompt = AIReviewClient(Settings(comps_db=str(tmp_path / "ai.sqlite")))._prompt(deal)
    assert "Whole advertisement:" in prompt
    assert "128 GB" in prompt
    assert "Extracted specs from the whole ad:" in prompt
    assert "128gb" in prompt
    assert "brand: Apple" in prompt
    assert "shortDescription: iPhone 13 128GB" in prompt


def test_ai_gate_keeps_buy_when_score_deadline_passed() -> None:
    class _Reviewer:
        def review(self, deal):
            raise AssertionError("should not review after the hunt score deadline")

    funnel = Counter()
    settings = Settings(ai_review_enabled=True, ai_review_required=True)
    result = _apply_ai_gate(
        [_deal()],
        settings,
        _Reviewer(),
        funnel,
        deadline=0.0,
    )[0]
    assert result.action.value == "buy"
    assert "AI review N/A" in result.reason
    assert "time cap" in result.reason
    assert funnel["ai_review_cap"] == 1

    near = _deal().model_copy(
        update={"action": Action.SKIP, "reason": "expected net profit 1 EUR < 9 EUR"}
    )
    skipped = _apply_ai_gate([near], settings, _Reviewer(), Counter(), deadline=0.0)[0]
    assert skipped.action.value == "skip"
    assert "time cap" in skipped.reason
    assert "AI review N/A" not in skipped.reason


def _approved_openai_payload() -> dict:
    return {
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
                                "quick_sale_price_eur": 120,
                                "confidence": 0.91,
                                "reason": "Exact model verified after Copilot quota.",
                                "source_urls": ["https://www.ebay.de/example-sold"],
                            }
                        ),
                        "annotations": [],
                    }
                ],
            }
        ]
    }


def test_unavailable_ai_keeps_buy_and_warns_in_alert() -> None:
    from bazar_deals.github_alerts import format_hunt_comment, select_buy_alerts
    from bazar_deals.pipeline import HuntRun

    class _Reviewer:
        def review(self, deal):
            raise RuntimeError("Copilot AI review failed: exceeded your monthly quota")

    deal = _deal()
    assert deal.action is Action.BUY
    funnel = Counter()
    settings = Settings(
        ai_review_enabled=True,
        ai_review_required=True,
        min_net_profit_eur=Decimal("9"),
    )
    result = _apply_ai_gate([deal], settings, _Reviewer(), funnel)[0]
    assert result.action is Action.BUY
    assert result.ai_review is None
    assert funnel["ai_unavailable"] == 1
    assert "AI review N/A" in result.reason
    assert "sk-" not in result.reason

    run = HuntRun(deals=[result], funnel=funnel, source_stats={})
    assert select_buy_alerts(run.deals, min_net_profit=Decimal("9")) == [result]
    body = format_hunt_comment(
        run,
        mention="babulic",
        min_profit=Decimal("9"),
        min_alert_profit=Decimal("9"),
        include_progress=False,
    )
    assert "AI review N/A" in body
    assert "- varovanie: AI review N/A" in body
    assert "BUY: áno" in body


def test_successful_ai_review_still_has_no_na_warning() -> None:
    class _Reviewer:
        def review(self, deal):
            return AIReview(
                approved=True,
                complete_product=True,
                canonical_name="Apple iPhone 13 128GB",
                kind="phones",
                quick_sale_price_eur=Decimal("120"),
                confidence=0.95,
                reason="Verified from sold comps.",
                source_urls=["https://www.ebay.de/example"],
                model="copilot:auto",
            )

    funnel = Counter()
    result = _apply_ai_gate(
        [_deal()],
        Settings(ai_review_enabled=True, ai_review_required=True, min_net_profit_eur=Decimal("9")),
        _Reviewer(),
        funnel,
    )[0]
    assert result.action is Action.BUY
    assert result.ai_review is not None
    assert funnel["ai_reviewed"] == 1
    assert "AI review N/A" not in result.reason


def test_copilot_quota_falls_back_to_openai(tmp_path, monkeypatch) -> None:
    calls = {"copilot": 0, "openai": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["openai"] += 1
        return httpx.Response(200, json=_approved_openai_payload())

    def boom(self, prompt: str) -> str:
        calls["copilot"] += 1
        raise RuntimeError("Copilot AI review failed: You have exceeded your monthly quota")

    monkeypatch.setattr(
        "bazar_deals.ai_review.shutil.which",
        lambda name: "/usr/bin/copilot" if name == "copilot" else None,
    )
    monkeypatch.setattr(AIReviewClient, "_run_copilot", boom)
    settings = Settings(
        ai_provider="copilot",
        openai_api_key="test-key",
        openai_model="gpt-5.6-terra",
        ai_review_enabled=True,
        ai_review_required=True,
        comps_db=str(tmp_path / "comps.sqlite"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        review = AIReviewClient(settings, client=client).review(_deal())

    assert calls["copilot"] == 1
    assert calls["openai"] == 1
    assert review.approved is True
    assert review.model == "gpt-5.6-terra"
    assert review.quick_sale_price_eur == Decimal("120.00")


def test_copilot_non_quota_failure_does_not_call_openai(tmp_path, monkeypatch) -> None:
    calls = {"openai": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["openai"] += 1
        return httpx.Response(200, json=_approved_openai_payload())

    def boom(self, prompt: str) -> str:
        raise RuntimeError("Copilot AI review failed: prompt rejected by policy")

    monkeypatch.setattr(
        "bazar_deals.ai_review.shutil.which",
        lambda name: "/usr/bin/copilot" if name == "copilot" else None,
    )
    monkeypatch.setattr(AIReviewClient, "_run_copilot", boom)
    settings = Settings(
        ai_provider="copilot",
        openai_api_key="test-key",
        comps_db=str(tmp_path / "comps.sqlite"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            AIReviewClient(settings, client=client).review(_deal())
        except RuntimeError as exc:
            assert "prompt rejected" in str(exc)
        else:
            raise AssertionError("non-quota Copilot failure should not be swallowed")
    assert calls["openai"] == 0


def test_ai_review_na_reason_redacts_key_shaped_text() -> None:
    from bazar_deals.ai_review import ai_review_na_reason

    reason = ai_review_na_reason("OpenAI fallback failed: invalid key sk-abcDEF1234567890")
    assert reason.startswith("AI review N/A:")
    assert "sk-abc" not in reason
