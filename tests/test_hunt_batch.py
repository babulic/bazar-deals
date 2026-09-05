from decimal import Decimal
from pathlib import Path
from collections import Counter
import json

import pytest
import httpx

from bazar_deals.cli import main
from bazar_deals.config import Settings
from bazar_deals.domain import Listing, Marketplace, Money
from bazar_deals.hunt_batch import BatchPage, HuntBatchStore, RemoteHuntBatchStore
from bazar_deals.pipeline import HuntRun, filter_usable_listings


def listing(index: int, *, marketplace: Marketplace = Marketplace.BAZOS) -> Listing:
    return Listing(
        marketplace=marketplace,
        external_id=str(index),
        title=f"Apple iPhone 13 128GB #{index}",
        description="Plne funkčný telefón, batéria 91 %, bez poškodenia.",
        url=f"https://mobil.bazos.sk/inzerat/{index}/",
        price=Money(amount=Decimal("40"), currency="EUR"),
    )


def test_batch_pages_are_stable_and_advance_monotonically(tmp_path: Path) -> None:
    store = HuntBatchStore(tmp_path / "batch.sqlite")
    created = store.replace(
        [listing(index) for index in range(5)],
        page_size=2,
        fetch_notes=["bazos: fetched 5"],
    )

    assert created.pending
    first = store.current_page()
    assert first is not None
    assert [item.external_id for item in first.listings] == ["0", "1"]
    assert (first.page, first.pages, first.remaining) == (1, 3, 3)
    assert first.fetch_notes == ["bazos: fetched 5"]

    advanced = store.advance(first)
    assert advanced.next_offset == 2
    second = store.current_page()
    assert second is not None
    assert [item.external_id for item in second.listings] == ["2", "3"]

    store.advance(second)
    final = store.current_page()
    assert final is not None
    assert [item.external_id for item in final.listings] == ["4"]
    status = store.advance(final)
    assert not status.pending
    assert store.current_page() is None
    assert store.needs_fetch()


def test_failed_page_is_retried_until_explicit_advance(tmp_path: Path) -> None:
    path = tmp_path / "batch.sqlite"
    store = HuntBatchStore(path)
    store.replace([listing(1), listing(2)], page_size=1)
    first = store.current_page()
    assert first is not None

    reopened = HuntBatchStore(path)
    retried = reopened.current_page()
    assert retried is not None
    assert retried.batch_id == first.batch_id
    assert retried.offset == first.offset
    assert retried.listings[0].external_id == "1"


def test_stale_page_cannot_advance_replaced_batch(tmp_path: Path) -> None:
    store = HuntBatchStore(tmp_path / "batch.sqlite")
    store.replace([listing(1)], page_size=1)
    stale = store.current_page()
    assert stale is not None
    store.replace([listing(2)], page_size=1)

    with pytest.raises(RuntimeError, match="stale"):
        store.advance(stale)


def test_replace_deduplicates_marketplace_external_id(tmp_path: Path) -> None:
    store = HuntBatchStore(tmp_path / "batch.sqlite")
    status = store.replace([listing(1), listing(1), listing(2)], page_size=80)
    assert status.total == 2
    page = store.current_page()
    assert page is not None
    assert [item.external_id for item in page.listings] == ["1", "2"]


@pytest.mark.parametrize("page_size", [0, -1])
def test_page_size_must_be_positive(tmp_path: Path, page_size: int) -> None:
    store = HuntBatchStore(tmp_path / "batch.sqlite")
    with pytest.raises(ValueError, match="positive"):
        store.replace([listing(1)], page_size=page_size)


def test_cli_resumes_next_page_without_new_listing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_path = tmp_path / "batch.sqlite"
    source_path = tmp_path / "listings.json"
    source_path.write_text(
        json.dumps(
            [listing(index).model_dump(mode="json") for index in range(3)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    scored: list[list[str]] = []

    def fake_score(rows, *args, **kwargs):
        scored.append([row.external_id for row in rows])
        return HuntRun(
            deals=[],
            funnel=Counter(usable=len(rows)),
            source_stats={},
            listings=list(rows),
        )

    monkeypatch.setattr("bazar_deals.cli.score_listings", fake_score)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )

    assert main(
        [
            "hunt",
            "--offline",
            "--batch-db",
            str(batch_path),
            "--batch-page-size",
            "2",
            "--listings-in",
            str(source_path),
        ]
    ) == 0
    source_path.unlink()
    assert main(["hunt", "--offline", "--batch-db", str(batch_path)]) == 0

    assert scored == [["0", "1"], ["2"]]
    status = HuntBatchStore(batch_path).status()
    assert status is not None
    assert status.next_offset == status.total == 3


def test_new_price_window_keeps_boundaries_and_rejects_outside() -> None:
    rows = [
        listing(index).model_copy(
            update={"price": Money(amount=amount, currency="EUR")}
        )
        for index, amount in enumerate(
            [Decimal("14.99"), Decimal("15"), Decimal("130"), Decimal("130.01")]
        )
    ]
    usable, _converted, funnel, _source_stats = filter_usable_listings(
        rows, Settings()
    )
    assert [item.price.amount for item in usable] == [Decimal("15"), Decimal("130")]
    assert funnel["under_min"] == 1
    assert funnel["over_cap"] == 1


def test_cli_does_not_checkpoint_page_when_price_query_budget_was_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_path = tmp_path / "batch.sqlite"
    source_path = tmp_path / "listings.json"
    source_path.write_text(
        json.dumps([listing(1).model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )
    attempts = 0

    def fake_score(rows, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        return HuntRun(
            deals=[],
            funnel=Counter(
                usable=len(rows),
                sold_lookup_cap=1 if attempts == 1 else 0,
            ),
            source_stats={},
            listings=list(rows),
        )

    monkeypatch.setattr("bazar_deals.cli.score_listings", fake_score)
    monkeypatch.setattr(
        "bazar_deals.cli.prepare_exchange_rates",
        lambda settings, offline=False: (settings, []),
    )

    args = [
        "hunt",
        "--offline",
        "--batch-db",
        str(batch_path),
        "--listings-in",
        str(source_path),
    ]
    assert main(args) == 0
    status = HuntBatchStore(batch_path).status()
    assert status is not None
    assert status.next_offset == 0

    source_path.unlink()
    assert main(["hunt", "--offline", "--batch-db", str(batch_path)]) == 0
    status = HuntBatchStore(batch_path).status()
    assert status is not None
    assert status.next_offset == status.total == 1


def test_remote_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"offset": 0, "rows": [], "batch_id": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/hunt/status":
            if not state["batch_id"]:
                return httpx.Response(200, json=None)
            return httpx.Response(
                200,
                json={
                    "batch_id": state["batch_id"],
                    "next_offset": state["offset"],
                    "total": len(state["rows"]),
                    "page_size": 1,
                },
            )
        if request.url.path == "/api/hunt/batches":
            payload = json.loads(request.content)
            state["batch_id"] = payload["batch_id"]
            state["rows"] = payload["listings"]
            return httpx.Response(
                200,
                json={
                    "batch_id": state["batch_id"],
                    "next_offset": 0,
                    "total": len(state["rows"]),
                    "page_size": 1,
                },
            )
        if request.url.path == "/api/hunt/page":
            return httpx.Response(
                200,
                json={
                    "batch_id": state["batch_id"],
                    "offset": state["offset"],
                    "total": len(state["rows"]),
                    "page_size": 1,
                    "listings": state["rows"][state["offset"] : state["offset"] + 1],
                    "fetch_notes": ["bazos: fetched 1"],
                },
            )
        payload = json.loads(request.content)
        state["offset"] = payload["offset"] + payload["count"]
        return httpx.Response(
            200,
            json={
                "batch_id": state["batch_id"],
                "next_offset": state["offset"],
                "total": len(state["rows"]),
                "page_size": 1,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "bazar_deals.hunt_batch.httpx.request",
        lambda method, url, **kwargs: client.request(method, url, **kwargs),
    )
    store = RemoteHuntBatchStore("https://store.example", "secret")
    assert store.needs_fetch()
    status = store.replace([listing(1)], page_size=1, fetch_notes=["bazos: fetched 1"])
    assert status.total == 1
    page = store.current_page()
    assert page is not None
    assert page.listings[0].external_id == "1"
    assert page.fetch_notes == ["bazos: fetched 1"]
    assert not store.advance(page).pending


def test_remote_advance_accepts_an_already_checkpointed_page(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"batch_id": "a" * 32, "offset": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/hunt/status":
            return httpx.Response(
                200,
                json={"batch_id": state["batch_id"], "next_offset": 1, "total": 1, "page_size": 1},
            )
        if request.url.path == "/api/hunt/advance":
            return httpx.Response(409)
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "bazar_deals.hunt_batch.httpx.request",
        lambda method, url, **kwargs: client.request(method, url, **kwargs),
    )
    store = RemoteHuntBatchStore("https://store.example", "secret")
    # Build the page directly because the service has already advanced it.
    page = BatchPage(
        batch_id=state["batch_id"], offset=0, total=1, page_size=1,
        listings=[listing(1)], fetch_notes=[],
    )
    assert not store.advance(page).pending
