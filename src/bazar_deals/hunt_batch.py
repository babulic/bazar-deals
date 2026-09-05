"""Persistent, monotonic paging for scheduled hunt listing batches."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

from bazar_deals.domain import Listing


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hunt_batch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    batch_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    next_offset INTEGER NOT NULL,
    total INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    fetch_notes_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hunt_batch_items (
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    listing_json TEXT NOT NULL,
    PRIMARY KEY (batch_id, position),
    UNIQUE (batch_id, marketplace, external_id)
);
CREATE INDEX IF NOT EXISTS idx_hunt_batch_items_position
ON hunt_batch_items(batch_id, position);
"""


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    next_offset: int
    total: int
    page_size: int

    @property
    def pending(self) -> bool:
        return self.next_offset < self.total


@dataclass(frozen=True)
class BatchPage:
    batch_id: str
    offset: int
    total: int
    page_size: int
    listings: list[Listing]
    fetch_notes: list[str]

    @property
    def page(self) -> int:
        return self.offset // self.page_size + 1

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def end(self) -> int:
        return self.offset + len(self.listings)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.end)


class HuntBatchStore:
    """SQLite queue whose cursor advances only after a successful notification."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()

    def status(self) -> BatchStatus | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT batch_id, next_offset, total, page_size "
                "FROM hunt_batch WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        return BatchStatus(
            batch_id=str(row["batch_id"]),
            next_offset=int(row["next_offset"]),
            total=int(row["total"]),
            page_size=int(row["page_size"]),
        )

    def needs_fetch(self) -> bool:
        status = self.status()
        return status is None or not status.pending

    def replace(
        self,
        listings: list[Listing],
        *,
        page_size: int,
        fetch_notes: list[str] | None = None,
    ) -> BatchStatus:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        batch_id = uuid.uuid4().hex
        unique: list[Listing] = []
        seen: set[tuple[str, str]] = set()
        for listing in listings:
            key = (listing.marketplace.value, listing.external_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(listing)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM hunt_batch_items")
            db.execute("DELETE FROM hunt_batch")
            db.execute(
                "INSERT INTO hunt_batch "
                "(singleton, batch_id, created_at, next_offset, total, page_size, fetch_notes_json) "
                "VALUES (1, ?, ?, 0, ?, ?, ?)",
                (
                    batch_id,
                    datetime.now(timezone.utc).isoformat(),
                    len(unique),
                    page_size,
                    json.dumps(fetch_notes or [], ensure_ascii=False),
                ),
            )
            db.executemany(
                "INSERT INTO hunt_batch_items "
                "(batch_id, position, marketplace, external_id, listing_json) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        batch_id,
                        position,
                        listing.marketplace.value,
                        listing.external_id,
                        json.dumps(listing.model_dump(mode="json"), ensure_ascii=False),
                    )
                    for position, listing in enumerate(unique)
                ],
            )
        return BatchStatus(batch_id, 0, len(unique), page_size)

    def current_page(self) -> BatchPage | None:
        with self._connect() as db:
            batch = db.execute(
                "SELECT batch_id, next_offset, total, page_size, fetch_notes_json "
                "FROM hunt_batch WHERE singleton = 1"
            ).fetchone()
            if batch is None or int(batch["next_offset"]) >= int(batch["total"]):
                return None
            rows = db.execute(
                "SELECT listing_json FROM hunt_batch_items "
                "WHERE batch_id = ? AND position >= ? "
                "ORDER BY position LIMIT ?",
                (
                    str(batch["batch_id"]),
                    int(batch["next_offset"]),
                    int(batch["page_size"]),
                ),
            ).fetchall()
        listings = [Listing.model_validate(json.loads(row["listing_json"])) for row in rows]
        return BatchPage(
            batch_id=str(batch["batch_id"]),
            offset=int(batch["next_offset"]),
            total=int(batch["total"]),
            page_size=int(batch["page_size"]),
            listings=listings,
            fetch_notes=list(json.loads(str(batch["fetch_notes_json"]))),
        )

    def advance(self, page: BatchPage) -> BatchStatus:
        if not page.listings:
            raise ValueError("cannot advance an empty page")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT batch_id, next_offset, total, page_size "
                "FROM hunt_batch WHERE singleton = 1"
            ).fetchone()
            if current is None:
                raise RuntimeError("hunt batch disappeared before checkpoint")
            if (
                str(current["batch_id"]) != page.batch_id
                or int(current["next_offset"]) != page.offset
            ):
                raise RuntimeError("hunt batch checkpoint is stale")
            next_offset = min(int(current["total"]), page.end)
            db.execute(
                "UPDATE hunt_batch SET next_offset = ? WHERE singleton = 1",
                (next_offset,),
            )
        return BatchStatus(
            batch_id=page.batch_id,
            next_offset=next_offset,
            total=page.total,
            page_size=page.page_size,
        )


class RemoteHuntBatchStore:
    """Deletion-aware queue backed by the private Alwyzon retention service."""

    def __init__(self, base_url: str, token: str) -> None:
        if not base_url.startswith("https://") or not token:
            raise ValueError("remote hunt batch store requires HTTPS URL and token")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = httpx.request(
            method,
            self.base_url + path,
            headers=self.headers,
            timeout=45,
            **kwargs,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _status(data: dict | None) -> BatchStatus | None:
        if data is None:
            return None
        return BatchStatus(
            batch_id=str(data["batch_id"]),
            next_offset=int(data["next_offset"]),
            total=int(data["total"]),
            page_size=int(data["page_size"]),
        )

    def status(self) -> BatchStatus | None:
        response = self._request("GET", "/api/hunt/status")
        return self._status(response.json() if response.content else None)

    def needs_fetch(self) -> bool:
        status = self.status()
        return status is None or not status.pending

    def replace(
        self,
        listings: list[Listing],
        *,
        page_size: int,
        fetch_notes: list[str] | None = None,
    ) -> BatchStatus:
        batch_id = uuid.uuid4().hex
        response = self._request(
            "POST",
            "/api/hunt/batches",
            json={
                "batch_id": batch_id,
                "page_size": page_size,
                "fetch_notes": fetch_notes or [],
                "listings": [
                    listing.model_dump(mode="json")
                    for listing in listings
                ],
            },
        )
        status = self._status(response.json())
        if status is None:
            raise RuntimeError("remote hunt store returned no batch status")
        return status

    def current_page(self) -> BatchPage | None:
        response = self._request("GET", "/api/hunt/page")
        if response.status_code == 204:
            return None
        data = response.json()
        return BatchPage(
            batch_id=str(data["batch_id"]),
            offset=int(data["offset"]),
            total=int(data["total"]),
            page_size=int(data["page_size"]),
            listings=[
                Listing.model_validate(item)
                for item in data["listings"]
            ],
            fetch_notes=[str(note) for note in data.get("fetch_notes", [])],
        )

    def advance(self, page: BatchPage) -> BatchStatus:
        response = self._request(
            "POST",
            "/api/hunt/advance",
            json={
                "batch_id": page.batch_id,
                "offset": page.offset,
                "count": len(page.listings),
            },
        )
        status = self._status(response.json())
        if status is None:
            raise RuntimeError("remote hunt store returned no checkpoint status")
        return status
