import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from bazar_deals.ebay_store import EbaySignatureVerifier, create_app

TOKEN = "test-admin-" + "a" * 40
VERIFICATION = "test-verification-" + "b" * 32
ENDPOINT = "https://example.com/ebay/account-deletion"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def service(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    checker = EbaySignatureVerifier(tmp_path / "credentials.json")
    checker.keys["test-key"] = (time.time() + 100, key.public_key())
    app = create_app({"EBAY_STORE_DIR": str(tmp_path), "EBAY_STORE_TOKEN": TOKEN,
                      "EBAY_VERIFICATION_TOKEN": VERIFICATION, "EBAY_NOTIFICATION_URL": ENDPOINT}, checker)
    return app, app.test_client(), key


def row():
    return {"url": "https://www.ebay.de/itm/123", "seller": "seller-canary", "title": "title-canary"}


def notification(key, *, username="seller-canary"):
    body = json.dumps({"metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
                       "notification": {"data": {"username": username, "userId": "user-canary"}}}, separators=(",", ":")).encode()
    signature = key.sign(body, ec.ECDSA(hashes.SHA1()))
    header = base64.b64encode(json.dumps({"kid": "test-key", "signature": base64.b64encode(signature).decode()}).encode()).decode()
    return body, {"x-ebay-signature": header, "Content-Type": "application/json"}


def test_exact_challenge_and_private_access(service):
    app, client, _ = service
    response = client.get("/ebay/account-deletion?challenge_code=challenge")
    assert response.json == {"challengeResponse": hashlib.sha256(("challenge" + VERIFICATION + ENDPOINT).encode()).hexdigest()}
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/batches", json={"epoch": 0, "records": [row()]}).status_code == 401
    assert client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [row()]}).status_code == 409
    assert client.get("/health").headers["Cache-Control"] == "no-store"


def test_signed_deletion_purges_storage_and_rejects_inflight_data(service):
    app, client, key = service
    assert client.post("/api/enable", headers=AUTH).status_code == 204
    assert client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [row()]}).json["saved"] == 1
    body, headers = notification(key)
    assert client.post("/ebay/account-deletion", data=body, headers=headers).status_code == 204
    store = app.extensions["ebay_store"]
    assert store.latest()["records"] == []
    assert b"title-canary" not in store.path.read_bytes()
    assert b"seller-canary" not in store.path.read_bytes()
    assert client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [row()]}).status_code == 409
    assert client.post("/api/batches", headers=AUTH, json={"epoch": 1, "records": [row()]}).json["saved"] == 0
    other = row() | {"seller": "other-seller", "title": "new-record"}
    client.post("/api/batches", headers=AUTH, json={"epoch": 1, "records": [other]})
    assert client.post("/ebay/account-deletion", data=body, headers=headers).status_code == 204
    assert len(store.latest()["records"]) == 1  # Replay must not purge a new batch.


def test_invalid_signature_cannot_delete_and_html_is_escaped(service):
    app, client, key = service
    client.post("/api/enable", headers=AUTH)
    record = row() | {"title": "<script>bad()</script>"}
    client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [record]})
    body, headers = notification(key)
    assert client.post("/ebay/account-deletion", data=body.replace(b"seller-canary", b"other-seller"), headers=headers).status_code == 412
    assert len(app.extensions["ebay_store"].latest()["records"]) == 1
    assert b"<script>bad()" not in client.get("/", headers=AUTH).data
    assert b"&lt;script&gt;" in client.get("/", headers=AUTH).data


def test_expired_records_are_removed_and_external_links_rejected(service):
    app, client, _ = service
    client.post("/api/enable", headers=AUTH)
    client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [row()]})
    store = app.extensions["ebay_store"]
    with store.connect() as db:
        db.execute("UPDATE batches SET created=?", (time.time() - 8 * 86400,))
    assert store.latest()["records"] == []
    assert client.post("/api/batches", headers=AUTH, json={"epoch": 0, "records": [row() | {"url": "javascript:alert(1)"}]}).status_code == 400


def test_storage_failure_is_not_acknowledged(service, monkeypatch):
    app, client, key = service
    def fail(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(app.extensions["ebay_store"], "purge", fail)
    body, headers = notification(key)
    assert client.post("/ebay/account-deletion", data=body, headers=headers).status_code == 503


def test_hunt_queue_pages_and_advances_atomically(service):
    _app, client, _key = service
    rows = [
        {
            "marketplace": "bazos",
            "external_id": str(index),
            "title": f"iPhone {index}",
            "url": f"https://mobil.bazos.sk/inzerat/{index}/",
            "price": {"amount": "40", "currency": "EUR"},
        }
        for index in range(3)
    ]
    payload = {
        "batch_id": "a" * 32,
        "page_size": 2,
        "fetch_notes": ["bazos: fetched 3"],
        "listings": rows,
    }
    assert client.get("/api/hunt/status").status_code == 401
    created = client.post("/api/hunt/batches", headers=AUTH, json=payload)
    assert created.status_code == 200
    assert created.json["next_offset"] == 0
    page = client.get("/api/hunt/page", headers=AUTH).json
    assert [row["external_id"] for row in page["listings"]] == ["0", "1"]
    assert page["fetch_notes"] == ["bazos: fetched 3"]

    assert client.post(
        "/api/hunt/advance",
        headers=AUTH,
        json={"batch_id": "b" * 32, "offset": 0, "count": 2},
    ).status_code == 409
    advanced = client.post(
        "/api/hunt/advance",
        headers=AUTH,
        json={"batch_id": "a" * 32, "offset": 0, "count": 2},
    )
    assert advanced.json["next_offset"] == 2
    page = client.get("/api/hunt/page", headers=AUTH).json
    assert [row["external_id"] for row in page["listings"]] == ["2"]
    client.post(
        "/api/hunt/advance",
        headers=AUTH,
        json={"batch_id": "a" * 32, "offset": 2, "count": 1},
    )
    assert client.get("/api/hunt/page", headers=AUTH).status_code == 204


def test_signed_ebay_deletion_purges_hunt_queue(service):
    app, client, key = service
    payload = {
        "batch_id": "a" * 32,
        "page_size": 1,
        "listings": [row()],
    }
    assert client.post("/api/hunt/batches", headers=AUTH, json=payload).status_code == 200
    body, headers = notification(key)
    assert client.post("/ebay/account-deletion", data=body, headers=headers).status_code == 204
    assert client.get("/api/hunt/status", headers=AUTH).json is None
    assert b"title-canary" not in app.extensions["ebay_store"].path.read_bytes()
