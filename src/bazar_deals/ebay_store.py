"""Private, deletable eBay evaluation snapshots and signed deletion receiver.

This store is deliberately separate from GitHub logs/comments and the comps DB.
On every verified account-deletion event it purges ALL eBay snapshots, advances
an epoch to reject in-flight batches and remembers hashed deleted identities.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from flask import Flask, Response, jsonify, redirect, render_template_string, request


class SnapshotStore:
    def __init__(self, path: Path, salt: str):
        self.path, self.salt = path, salt.encode()
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(self.salt).digest()))
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, epoch INTEGER, enabled INTEGER);
                INSERT OR IGNORE INTO state VALUES (1, 0, 0);
                CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, created REAL, payload TEXT);
                CREATE TABLE IF NOT EXISTS deleted (digest TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS events (digest TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS hunt_queue (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    batch_id TEXT NOT NULL,
                    created REAL NOT NULL,
                    next_offset INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    page_size INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        try:
            with db:
                yield db
        finally:
            db.close()

    def identity(self, value):
        return hmac.new(self.salt, str(value).strip().casefold().encode(), hashlib.sha256).hexdigest()

    def status(self):
        with self.connect() as db:
            db.execute("DELETE FROM batches WHERE created < ?", (time.time() - 7 * 86400,))
            epoch, enabled = db.execute("SELECT epoch, enabled FROM state WHERE id=1").fetchone()
        return {"epoch": epoch, "enabled": bool(enabled)}

    def save(self, epoch, records):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current, enabled = db.execute("SELECT epoch, enabled FROM state WHERE id=1").fetchone()
            if not enabled or epoch != current:
                raise ValueError("retention disabled or stale batch")
            blocked = {row[0] for row in db.execute("SELECT digest FROM deleted")}
            # Unknown seller identities cannot be safely excluded after a deletion.
            clean = [r for r in records if r.get("seller") and self.identity(r["seller"]) not in blocked
                     and (not r.get("seller_id") or self.identity(r["seller_id"]) not in blocked)]
            db.execute("INSERT INTO batches(created,payload) VALUES (?,?)", (time.time(), self.cipher.encrypt(json.dumps(clean).encode()).decode()))
            db.execute("DELETE FROM batches WHERE created < ?", (time.time() - 7 * 86400,))
            return len(clean)

    def purge(self, identities, event_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            event = self.identity(event_id)
            if db.execute("SELECT 1 FROM events WHERE digest=?", (event,)).fetchone():
                return
            db.execute("INSERT INTO events VALUES (?)", (event,))
            db.execute("DELETE FROM batches")
            db.execute("DELETE FROM hunt_queue")
            db.execute("UPDATE state SET epoch=epoch+1 WHERE id=1")
            for identity in identities:
                if identity:
                    db.execute("INSERT OR IGNORE INTO deleted VALUES (?)", (self.identity(identity),))
        with self.connect() as db:
            db.execute("VACUUM")

    def latest(self):
        self.status()
        with self.connect() as db:
            row = db.execute("SELECT created,payload FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        return {"created": row[0], "records": json.loads(self.cipher.decrypt(row[1].encode()))} if row else {"records": []}

    def hunt_status(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT batch_id,next_offset,total,page_size FROM hunt_queue WHERE singleton=1"
            ).fetchone()
        if row is None:
            return None
        return {
            "batch_id": str(row[0]),
            "next_offset": int(row[1]),
            "total": int(row[2]),
            "page_size": int(row[3]),
        }

    def replace_hunt(self, batch_id, listings, page_size, fetch_notes):
        payload = self.cipher.encrypt(
            json.dumps(
                {"listings": listings, "fetch_notes": fetch_notes},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).decode()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT next_offset,total FROM hunt_queue WHERE singleton=1"
            ).fetchone()
            if current is not None and int(current[0]) < int(current[1]):
                raise ValueError("hunt batch is still pending")
            db.execute("DELETE FROM hunt_queue")
            db.execute(
                "INSERT INTO hunt_queue "
                "(singleton,batch_id,created,next_offset,total,page_size,payload) "
                "VALUES (1,?,?,0,?,?,?)",
                (batch_id, time.time(), len(listings), page_size, payload),
            )
        return self.hunt_status()

    def hunt_page(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT batch_id,next_offset,total,page_size,payload "
                "FROM hunt_queue WHERE singleton=1"
            ).fetchone()
        if row is None or int(row[1]) >= int(row[2]):
            return None
        payload = json.loads(self.cipher.decrypt(str(row[4]).encode()))
        start, size = int(row[1]), int(row[3])
        listings = payload["listings"][start : start + size]
        return {
            "batch_id": str(row[0]),
            "offset": start,
            "total": int(row[2]),
            "page_size": size,
            "listings": listings,
            "fetch_notes": payload.get("fetch_notes", []),
        }

    def advance_hunt(self, batch_id, offset, count):
        if count < 1:
            raise ValueError("invalid page count")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT batch_id,next_offset,total,page_size FROM hunt_queue WHERE singleton=1"
            ).fetchone()
            if row is None or str(row[0]) != batch_id or int(row[1]) != offset:
                raise ValueError("stale hunt checkpoint")
            next_offset = min(int(row[2]), offset + count)
            db.execute(
                "UPDATE hunt_queue SET next_offset=? WHERE singleton=1",
                (next_offset,),
            )
        return {
            "batch_id": batch_id,
            "next_offset": next_offset,
            "total": int(row[2]),
            "page_size": int(row[3]),
        }


class EbaySignatureVerifier:
    def __init__(self, credentials: Path, secret: str = ""):
        self.credentials = credentials
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        self.keys = {}
        self.lookup_lock = threading.Lock()
        self.next_lookup = 0.0

    def key(self, kid):
        if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", kid):
            raise ValueError("invalid key identifier")
        cached = self.keys.get(kid)
        if cached and cached[0] > time.time():
            return cached[1]
        # Unauthenticated webhook traffic must not exhaust OAuth/API quotas by
        # inventing unlimited key IDs. A real notification can retry on 503.
        with self.lookup_lock:
            if time.monotonic() < self.next_lookup:
                raise RuntimeError("public key lookup temporarily limited")
            self.next_lookup = time.monotonic() + 60
        config = json.loads(self.cipher.decrypt(self.credentials.read_bytes()))
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            response = client.post("https://api.ebay.com/identity/v1/oauth2/token",
                auth=(config["client_id"], config["client_secret"]),
                data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
            response.raise_for_status()
            response = client.get("https://api.ebay.com/commerce/notification/v1/public_key/" + kid,
                headers={"Authorization": "Bearer " + response.json()["access_token"]})
            response.raise_for_status()
            public = serialization.load_pem_public_key(response.json()["key"].encode())
        if len(self.keys) >= 100:
            self.keys.clear()
        self.keys[kid] = (time.time() + 3600, public)
        return public

    def verify(self, body, header):
        if not header or len(header) > 8192:
            return False
        data = json.loads(base64.b64decode(header, validate=True))
        signature = base64.b64decode(data["signature"], validate=True)
        key = self.key(data["kid"])
        # eBay's official event-notification SDK uses SHA-1 signatures and a
        # compact JSON serialization. Accept raw bytes or that serialization.
        compact = json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":")).encode()
        for message in (body, compact):
            try:
                if isinstance(key, ec.EllipticCurvePublicKey):
                    key.verify(signature, message, ec.ECDSA(hashes.SHA1()))
                elif isinstance(key, rsa.RSAPublicKey):
                    key.verify(signature, message, padding.PKCS1v15(), hashes.SHA1())
                else:
                    return False
                return True
            except InvalidSignature:
                continue
        return False


def create_app(config=None, verifier=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=20_000_000)
    cfg = config or os.environ
    root = Path(cfg.get("EBAY_STORE_DIR", "/data"))
    token = cfg["EBAY_STORE_TOKEN"]
    verification = cfg["EBAY_VERIFICATION_TOKEN"]
    endpoint = cfg["EBAY_NOTIFICATION_URL"]
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", verification):
        raise ValueError("invalid store configuration")
    store = SnapshotStore(root / "snapshots.sqlite", token)
    checker = verifier or EbaySignatureVerifier(root / "credentials.enc", token)
    app.extensions["ebay_store"] = store
    if config is None:
        def expire_snapshots():
            while True:
                time.sleep(900)
                try:
                    store.status()
                except sqlite3.Error:
                    pass  # Retry next cycle; responses never enter logs.
        threading.Thread(target=expire_snapshots, daemon=True).start()

    def authorized():
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        supplied = supplied or request.cookies.get("ebay_access", "")
        return hmac.compare_digest(supplied, token)

    @app.after_request
    def secure_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        return response

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.route("/ebay/account-deletion", methods=["GET", "POST"])
    def deletion():
        if request.method == "GET":
            challenge = request.args.get("challenge_code", "")
            if not challenge or len(challenge) > 512:
                return "", 400
            digest = hashlib.sha256((challenge + verification + endpoint).encode()).hexdigest()
            return jsonify(challengeResponse=digest)
        raw = request.get_data()
        try:
            if not checker.verify(raw, request.headers.get("x-ebay-signature", "")):
                return "", 412
            payload = json.loads(raw)
            if payload.get("metadata", {}).get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
                return "", 400
            data = payload["notification"]["data"]
            identities = [data.get(key) for key in ("username", "userId", "eiasToken")]
            if not any(identities):
                return "", 400
            event_id = payload["notification"].get("notificationId") or hashlib.sha256(raw).hexdigest()
            store.purge(identities, event_id)
            return "", 204
        except (ValueError, KeyError, TypeError, InvalidSignature):
            return "", 412
        except Exception:
            # Do not acknowledge storage/network failures: eBay must retry.
            # No request bodies, identifiers or credentials in error logs.
            return "", 503

    @app.get("/api/status")
    def status():
        return jsonify(store.status()) if authorized() else ("", 401)

    @app.post("/api/credentials")
    def credentials():
        if not authorized():
            return "", 401
        data = request.get_json()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and 1 <= len(data[k]) <= 1000
                                               for k in ("client_id", "client_secret")):
            return "", 400
        target = root / "credentials.enc"
        temporary = root / "credentials.new"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(store.cipher.encrypt(json.dumps({key: data[key] for key in ("client_id", "client_secret")}).encode()))
        os.replace(temporary, target)
        return "", 204

    @app.post("/api/enable")
    def enable():
        if not authorized():
            return "", 401
        # Used only after real notification registration and test, never by a scheduler.
        with store.connect() as db:
            db.execute("UPDATE state SET enabled=1 WHERE id=1")
        return "", 204

    @app.post("/api/batches")
    def batches():
        if not authorized():
            return "", 401
        data = request.get_json()
        if not isinstance(data, dict) or type(data.get("epoch")) is not int:
            return "", 400
        rows = data.get("records")
        if not isinstance(rows, list) or len(rows) > 2000 or any(not isinstance(r, dict) for r in rows):
            return "", 400
        # Restrict links rendered by the dashboard to actual eBay item pages.
        for row in rows:
            url = str(row.get("url", ""))
            if not re.fullmatch(r"https://(?:www\.)?ebay\.(?:de|at|com|fr|it|pl|nl|es|be)/itm/[^\s<>]+", url):
                return "", 400
        try:
            return jsonify(saved=store.save(data["epoch"], rows))
        except ValueError:
            return "", 409

    @app.get("/api/hunt/status")
    def hunt_status():
        if not authorized():
            return "", 401
        return jsonify(store.hunt_status())

    @app.get("/api/hunt/page")
    def hunt_page():
        if not authorized():
            return "", 401
        page = store.hunt_page()
        return (jsonify(page), 200) if page is not None else ("", 204)

    @app.post("/api/hunt/batches")
    def hunt_batches():
        if not authorized():
            return "", 401
        data = request.get_json()
        if not isinstance(data, dict):
            return "", 400
        listings = data.get("listings")
        notes = data.get("fetch_notes", [])
        page_size = data.get("page_size")
        batch_id = data.get("batch_id")
        if (
            not isinstance(batch_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", batch_id)
            or type(page_size) is not int
            or not 1 <= page_size <= 80
            or not isinstance(listings, list)
            or len(listings) > 20_000
            or any(not isinstance(row, dict) for row in listings)
            or not isinstance(notes, list)
            or any(not isinstance(note, str) or len(note) > 2000 for note in notes)
        ):
            return "", 400
        try:
            return jsonify(store.replace_hunt(batch_id, listings, page_size, notes))
        except ValueError:
            return "", 409

    @app.post("/api/hunt/advance")
    def hunt_advance():
        if not authorized():
            return "", 401
        data = request.get_json()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("batch_id"), str)
            or type(data.get("offset")) is not int
            or type(data.get("count")) is not int
        ):
            return "", 400
        try:
            return jsonify(
                store.advance_hunt(
                    data["batch_id"],
                    data["offset"],
                    data["count"],
                )
            )
        except ValueError:
            return "", 409

    @app.route("/", methods=["GET", "POST"])
    def dashboard():
        if request.method == "POST":
            if not hmac.compare_digest(request.form.get("password", ""), token):
                return "Invalid access token", 401
            response = redirect("/")
            response.set_cookie("ebay_access", token, secure=True, httponly=True, samesite="Strict", max_age=3600)
            return response
        if not authorized():
            return '<h1>eBay evaluation</h1><form method="post"><label>Access token <input name="password" type="password"></label><button>Open</button></form>'
        data = store.latest()
        return render_template_string('''<!doctype html><meta charset="utf-8"><title>eBay evaluation</title>
            <style>body{font:16px system-ui;margin:32px;max-width:1300px}td,th{padding:9px;border-bottom:1px solid #ccc;text-align:left}a{color:#075}</style>
            <h1>eBay — uložené výsledky testu</h1>
            <p>Aktívne predajné ponuky sú porovnanie konkurencie, nie potvrdení kupci ani predané ceny.
            Nákupní kandidáti ešte nie sú schválené BUY. Údaje najviac 7 dní; pri oznámení o vymazaní sa celý prehľad vyčistí.</p>
            <p>{{ records|length }} uložených záznamov</p>
            <table><tr><th>Typ</th><th>Sklad / hľadanie</th><th>Ponuka</th><th>Cena</th><th>Doprava</th></tr>
            {% for r in records %}<tr><td>{{ r.kind }}</td><td>{{ r.stock_id or r.query }}</td>
            <td><a href="{{ r.url }}" rel="noreferrer noopener" target="_blank">{{ r.title }}</a></td>
            <td>{{ r.price }} {{ r.currency }}</td><td>{{ r.shipping }}</td></tr>{% endfor %}</table>''', **data)

    return app
