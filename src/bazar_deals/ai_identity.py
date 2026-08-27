from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field

from bazar_deals.ai_review import AIReviewClient, _fold, _iso, _json_payload, _parse_iso, _utc_now
from bazar_deals.config import Settings
from bazar_deals.domain import IdentifiedItem, ItemKind, Listing
from bazar_deals.identity import ItemSpecs, extract_specs, listing_text, with_specs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_identities (
    listing_key TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    search_query TEXT NOT NULL,
    specs TEXT,
    confidence REAL NOT NULL,
    model TEXT,
    identified_at TEXT NOT NULL
);
"""

_PROMPT = """You identify second-hand marketplace items so they can be priced
against completed sales. Read the whole advertisement, including the body text,
not just the headline. Sellers often leave the decisive detail out of the title.

--- LISTING START ---
Marketplace: {marketplace}
Title: {title}
Asking price: {price} {currency}
Body:
{description}
--- LISTING END ---

Work out exactly which product this is. Pay attention to storage capacity,
production year, chip or part numbers, model codes, generation and variant
names, and whether the ad sells a single piece or a multi-piece lot.

Return JSON only, no markdown, with exactly these keys:
{{
  "canonical_name": "manufacturer and exact model as a collector would write it",
  "kind": "one of: {kinds}",
  "search_query": "3-7 words to search completed listings for this exact product",
  "storage": ["capacity tokens such as 128gb, empty if not applicable"],
  "model_codes": ["part or model codes such as 8565r2, empty if none"],
  "years": ["4-digit production years stated in the ad, empty if none"],
  "lot_size": 1,
  "confidence": 0.0,
  "reason": "one sentence on what in the ad settled the identification"
}}

If the advertisement does not say enough to name the product, set confidence to
0 and leave search_query empty. Never guess a model you cannot support from the
text."""


class AIIdentity(BaseModel):
    canonical_name: str
    kind: str
    search_query: str
    specs: ItemSpecs = Field(default_factory=ItemSpecs)
    confidence: float = 0.0
    reason: str = ""
    model: str = ""
    cached: bool = False


def listing_key(listing: Listing) -> str:
    return f"{listing.marketplace.value}:{listing.external_id or _fold(listing.title)}"


class AIIdentityClient:
    """Names an item the deterministic rules could not, using free Copilot.

    This decides *what* the thing is, never what it is worth. The valuation
    still comes from completed sales, and a candidate must still clear the
    net-profit floor and the fail-closed AI price review before it can be a BUY.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        reviewer: AIReviewClient | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._reviewer = reviewer or AIReviewClient(self.settings, db_path=db_path)
        self._db_path = Path(db_path) if db_path is not None else Path(self.settings.comps_db)
        self._init_db()

    def identify(self, listing: Listing) -> AIIdentity | None:
        key = listing_key(listing)
        cached = self._load(key)
        if cached is not None:
            return cached

        prompt = _PROMPT.format(
            marketplace=listing.marketplace.value,
            title=listing.title,
            price=listing.price.amount,
            currency=listing.price.currency,
            description=(listing_text(listing) or listing.title)[:4000],
            kinds=", ".join(kind.value for kind in ItemKind),
        )
        text, _urls, model = self._reviewer.complete(prompt)
        raw = _json_payload(text)

        query = str(raw.get("search_query") or "").strip()
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
        if not query or confidence < self.settings.ai_min_confidence:
            return None

        kind = str(raw.get("kind") or "generic").strip()
        if kind not in {item.value for item in ItemKind}:
            kind = "generic"

        identity = AIIdentity(
            canonical_name=str(raw.get("canonical_name") or listing.title).strip(),
            kind=kind,
            search_query=query,
            specs=_specs_from(raw, listing),
            confidence=confidence,
            reason=str(raw.get("reason") or "").strip(),
            model=model,
        )
        self._store(key, identity)
        return identity

    def apply(self, listing: Listing, item: IdentifiedItem) -> IdentifiedItem | None:
        identity = self.identify(listing)
        if identity is None:
            return None
        query = with_specs(identity.search_query, identity.specs)
        return item.model_copy(
            update={
                "canonical_name": identity.canonical_name,
                "kind": identity.kind,
                "model": query,
                "search_query": query,
                "specs": identity.specs,
                "identified_by": identity.model or "ai",
                "confidence": identity.confidence,
            }
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _load(self, key: str) -> AIIdentity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT canonical_name, kind, search_query, specs, confidence, model, "
                "identified_at FROM ai_identities WHERE listing_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        stamped = _parse_iso(str(row["identified_at"]))
        if stamped is None:
            return None
        if _utc_now() - stamped > timedelta(days=max(0, int(self.settings.ai_review_ttl_days))):
            return None
        try:
            specs = ItemSpecs(**json.loads(row["specs"] or "{}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            specs = ItemSpecs()
        return AIIdentity(
            canonical_name=str(row["canonical_name"]),
            kind=str(row["kind"]),
            search_query=str(row["search_query"]),
            specs=specs,
            confidence=float(row["confidence"]),
            model=str(row["model"] or "ai"),
            cached=True,
        )

    def _store(self, key: str, identity: AIIdentity) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_identities (listing_key, canonical_name, kind, search_query, "
                "specs, confidence, model, identified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(listing_key) DO UPDATE SET "
                "canonical_name=excluded.canonical_name, kind=excluded.kind, "
                "search_query=excluded.search_query, specs=excluded.specs, "
                "confidence=excluded.confidence, model=excluded.model, "
                "identified_at=excluded.identified_at",
                (
                    key,
                    identity.canonical_name,
                    identity.kind,
                    identity.search_query,
                    json.dumps(identity.specs.model_dump(mode="json")),
                    identity.confidence,
                    identity.model,
                    _iso(_utc_now()),
                ),
            )


def _specs_from(raw: dict, listing: Listing) -> ItemSpecs:
    """Trust the model on facts it can quote, but keep the rule-based floor."""
        mined = extract_specs(listing_text(listing))

    def tokens(key: str) -> frozenset[str]:
        values = raw.get(key)
        if not isinstance(values, list):
            return frozenset()
        return frozenset(_fold(str(value)) for value in values if str(value).strip())

    lot = raw.get("lot_size")
    lot_size = int(lot) if isinstance(lot, int) and 1 < lot <= 500 else mined.lot_size
    return ItemSpecs(
        storage=tokens("storage") or mined.storage,
        years=tokens("years") or mined.years,
        variants=mined.variants,
        phone=mined.phone,
        model_codes=tokens("model_codes") or mined.model_codes,
        lot_size=lot_size,
        localities=mined.localities,
    )


__all__ = ["AIIdentity", "AIIdentityClient", "listing_key"]
