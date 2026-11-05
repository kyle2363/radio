import asyncio
import base64
import json
import secrets
from uuid import uuid4

import httpx
import pytest

from db import database
from portable_sync import PortableProfileStore, SyncConflict
from portable_pairing import PairingStore
from mobile_identity import ServerIdentity
from mobile_gateway import create_mobile_gateway


class Protector:
    def protect(self, value):
        return b"protected:" + value

    def unprotect(self, value):
        if not value.startswith(b"protected:"):
            raise RuntimeError("key unavailable")
        return value[10:]


def setup(tmp_path):
    store = PortableProfileStore()
    identity = ServerIdentity(tmp_path / "identity", protector=Protector())
    pairing = PairingStore(store, identity.server_id)
    return store, identity, pairing


def body(ticket):
    return {"protocol_version": 1, "ticket_id": ticket["ticket_id"],
            "ticket_secret": ticket["ticket_secret"], "exchange_id": str(uuid4()),
            "device_name": "Phone", "candidate_bearer": secrets.token_urlsafe(32)}


def request(app, method, path, headers=None, content=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:18766") as client:
            return await client.request(method, path, headers=headers, content=content)
    return asyncio.run(run())


def test_identity_persists_and_signature_authenticates_exact_bytes(tmp_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.exceptions import InvalidSignature
    _, identity, _ = setup(tmp_path)
    again = ServerIdentity(tmp_path / "identity", protector=Protector())
    assert (identity.server_id, identity.public_key_pin) == (again.server_id, again.public_key_pin)
    envelope = identity.descriptor("http://127.0.0.1:18766", 1, 1)
    decode = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    key = serialization.load_der_public_key(decode(envelope["public_key"]))
    payload = decode(envelope["payload"])
    key.verify(decode(envelope["signature"]), payload, ec.ECDSA(hashes.SHA256()))
    assert json.loads(payload)["server_id"] == identity.server_id
    with pytest.raises(InvalidSignature):
        key.verify(decode(envelope["signature"]), payload + b" ", ec.ECDSA(hashes.SHA256()))
    (tmp_path / "identity").write_bytes(b"broken")
    with pytest.raises(RuntimeError):
        ServerIdentity(tmp_path / "identity", protector=Protector())


def test_ticket_exact_retry_atomic_and_no_plaintext_secrets(tmp_path):
    store, identity, pairing = setup(tmp_path)
    ticket = pairing.create_ticket("Phone")
    exchange = body(ticket)
    first = pairing.exchange(exchange)
    assert pairing.exchange(exchange) == first
    assert first["scopes"] == ["catalog:read"]
    assert store.authorize(exchange["candidate_bearer"])["device_id"] == first["device_id"]
    with pytest.raises(SyncConflict):
        pairing.exchange(dict(exchange, exchange_id=str(uuid4())))
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT COUNT(*) FROM portable_devices").fetchone()[0] == 1
        dump = "\n".join(conn.iterdump())
    assert ticket["ticket_secret"] not in dump
    assert exchange["candidate_bearer"] not in dump
    store.revoke_device(first["device_id"])
    assert store.authorize(exchange["candidate_bearer"]) is None
    with pytest.raises(ValueError):
        pairing.exchange(exchange)


def test_ticket_expiry_metadata_and_bearer_validation(tmp_path):
    _, _, pairing = setup(tmp_path)
    now = [1000]
    pairing.clock = lambda: now[0]
    exchange = body(pairing.create_ticket("Phone"))
    for change in ({"device_name": "Other"}, {"candidate_bearer": "x" * 42}, {"scopes": ["profile:write"]}):
        with pytest.raises(ValueError):
            pairing.exchange(dict(exchange, **change))
    now[0] += 301
    with pytest.raises(ValueError):
        pairing.exchange(exchange)


def test_wire_expiry_integer_even_with_fractional_clock(tmp_path):
    _, _, pairing = setup(tmp_path)
    pairing.clock = lambda: 1000.75
    ticket = pairing.create_ticket("Phone")
    assert type(ticket["expires_at"]) is int
    assert type(pairing.exchange(body(ticket))["expires_at"]) is int


def test_pairing_migration_backs_up_once_and_preserves_legacy_device(tmp_path):
    import sqlite3
    from pathlib import Path
    store = PortableProfileStore()
    legacy = store.issue_device("Existing", ["profile:read"])
    backups = Path(database.DB_PATH).parent / "backups"
    assert not list(backups.glob("radio-before-pairing-*.db"))
    pairing = PairingStore(store, "synthetic-server")
    saved = list(backups.glob("radio-before-pairing-*.db"))
    assert len(saved) == 1
    with sqlite3.connect(str(saved[0])) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT id FROM portable_devices").fetchone()[0] == legacy["device_id"]
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_pairing_tickets'").fetchone()
    assert store.authorize(legacy["token"])["device_id"] == legacy["device_id"]
    PairingStore(store, "synthetic-server")
    assert len(list(backups.glob("radio-before-pairing-*.db"))) == 1
    assert pairing.devices()[0]["name"] == "Existing"


def test_exchange_rolls_back_device_if_receipt_write_fails(tmp_path):
    _, _, pairing = setup(tmp_path)
    exchange = body(pairing.create_ticket("Phone"))
    with database.get_connection_cm() as conn:
        conn.execute("CREATE TRIGGER fail_receipt BEFORE UPDATE ON portable_pairing_tickets BEGIN SELECT RAISE(ABORT,'injected receipt failure'); END")
        conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        pairing.exchange(exchange)
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT COUNT(*) FROM portable_devices").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_receipt")
        conn.commit()
    assert pairing.exchange(exchange)["device_id"]


def test_gateway_only_explicit_routes_and_mandatory_loopback_auth(tmp_path):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    assert request(app, "GET", "/api/portable/v1/identity").status_code == 503
    app.state.admission.enable(1)
    path = "/api/portable/v1/capabilities"
    assert request(app, "GET", path).status_code == 401
    grant = pairing.exchange(body(pairing.create_ticket("Phone")))
    token = store.issue_device("Other", ["catalog:read"])["token"]
    headers = {"Authorization": "Bearer " + token}
    assert request(app, "GET", path, headers).status_code == 200
    assert request(app, "GET", "/api/portable/v1/identity").status_code == 200
    for path in ("/docs", "/openapi.json", "/api/settings", "/api/mobile-hosting/devices", "/api/portable/v1/devices", "/api/portable/v1/catalog"):
        assert request(app, "GET", path, headers).status_code == 404
    store.revoke_device(grant["device_id"])
    store.revoke_device(store.authorize(token)["device_id"])
    assert request(app, "GET", "/api/portable/v1/capabilities", headers).status_code == 401
    app.state.admission.disable()
    assert request(app, "GET", "/api/portable/v1/identity").status_code == 503


def test_gateway_profile_sync_is_scoped_and_reports_authenticated_capabilities(tmp_path):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    device = store.issue_device("Catalog only", ["catalog:read"])
    headers = {"Authorization": "Bearer " + device["token"]}
    capabilities = request(app, "GET", "/api/portable/v1/capabilities", headers)
    assert capabilities.status_code == 200
    assert capabilities.json()["profile_sync"] is True
    assert capabilities.json()["scopes"] == ["catalog:read"]
    for method, path, content in (("GET", "/api/portable/v1/events", None),
                                  ("GET", "/api/portable/v1/profile", None),
                                  ("POST", "/api/portable/v1/events", b'{"protocol_version":1,"events":[]}')):
        assert request(app, method, path, headers, content).status_code == 403

    store.grant_profile_sync(device["device_id"])
    scopes = store.authorize(device["token"])["scopes"]
    assert scopes == ["catalog:read", "profile:read", "profile:write"]
    assert request(app, "GET", "/api/portable/v1/capabilities", headers).json()["scopes"] == scopes
    assert request(app, "GET", "/api/portable/v1/events?cursor=0&limit=100", headers).status_code == 200
    assert request(app, "GET", "/api/portable/v1/profile", headers).status_code == 200
    headers = {**headers, "Content-Type": "application/json"}
    assert request(app, "POST", "/api/portable/v1/events", headers, b'{"protocol_version":1,"events":[]}').status_code == 422
    event = {"protocol_version": 1, "event_id": "phone-event-1", "device_id": device["device_id"],
             "logical_clock": 1, "catalog_id": store.catalog_id, "kind": "favorite_changed",
             "song_id": "1", "occurred_at": 1793220000, "value": True}
    imported = request(app, "POST", "/api/portable/v1/events", headers,
                       json.dumps({"protocol_version": 1, "events": [event]}).encode())
    assert imported.status_code == 200
    assert imported.json()["accepted"] == ["phone-event-1"]
    assert request(app, "GET", "/api/portable/v1/profile", headers).json()["favorites"]["1"] is True


@pytest.mark.parametrize("query", ["cursor=-1", "cursor=01", "cursor=0&cursor=1", "limit=501", "limit=1&limit=2", "cursor=0&x=1"])
def test_gateway_sync_query_is_strict(tmp_path, query):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    device = store.issue_device("Phone", ["profile:read"])
    headers = {"Authorization": "Bearer " + device["token"]}
    assert request(app, "GET", "/api/portable/v1/events?" + query, headers).status_code == 400


def test_local_admin_upgrade_preserves_existing_device_and_token(tmp_path):
    store, _, _ = setup(tmp_path)
    device = store.issue_device("Phone", ["catalog:read"])
    upgraded = store.grant_profile_sync(device["device_id"])
    assert upgraded["device_id"] == device["device_id"]
    assert upgraded["scopes"] == ["catalog:read", "profile:read", "profile:write"]
    assert store.authorize(device["token"])["device_id"] == device["device_id"]
    with pytest.raises(ValueError):
        store.grant_profile_sync("missing-device")


def test_gateway_sync_parsers_reject_ambiguous_queries_and_duplicate_json_fields():
    from mobile_gateway import strict_json, sync_query
    assert sync_query(b"limit=25&cursor=12") == {"cursor": 12, "limit": 25}
    for query in (b"cursor=-1", b"cursor=01", b"cursor=0&cursor=1", b"limit=501", b"limit=1&limit=2", b"cursor=0&unknown=1", b"cursor=%30", b"cursor=9223372036854775808"):
        with pytest.raises(ValueError):
            sync_query(query)
    assert strict_json(b'{"protocol_version":1,"events":[]}') == {"protocol_version": 1, "events": []}
    for raw in (b'{"protocol_version":1,"protocol_version":1,"events":[]}',
                b'{"protocol_version":1,"events":[{"event_id":"a","event_id":"b"}]}',
                b'{"protocol_version":NaN,"events":[]}'):
        with pytest.raises(ValueError):
            strict_json(raw)


def test_gateway_sync_rejects_oversized_body_and_duplicate_json_keys(tmp_path):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    device = store.issue_device("Phone", ["profile:write"])
    headers = {"Authorization": "Bearer " + device["token"], "Content-Type": "application/json"}
    path = "/api/portable/v1/events"
    assert request(app, "POST", path, headers, b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    duplicate = b'{"protocol_version":1,"events":[],"events":[]}'
    assert request(app, "POST", path, headers, duplicate).status_code == 422


@pytest.mark.parametrize("headers,status", [({"Host": "evil.test"}, 400), ({"Origin": "null"}, 403),
    ({"Cookie": "radio_session=x"}, 403), ({"X-Forwarded-Host": "evil"}, 400),
    ({"Authorization": "Bearer bad"}, 401), ([("Host", "127.0.0.1:18766"), ("Host", "127.0.0.1:18766")], 400)])
def test_gateway_bootstrap_rejects_ambiguous_authority(tmp_path, headers, status):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    assert request(app, "GET", "/api/portable/v1/identity", headers).status_code == status


def test_gateway_exchange_limits_and_scope(tmp_path):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    path = "/api/portable/v1/pairing/exchange"
    assert request(app, "POST", path, {"Content-Type": "application/json"}, b"x" * 8193).status_code == 413
    exchange = body(pairing.create_ticket("Phone"))
    response = request(app, "POST", path, {"Content-Type": "application/json"}, json.dumps(exchange))
    assert response.status_code == 200
    assert response.json() == request(app, "POST", path, {"Content-Type": "application/json"}, json.dumps(exchange)).json()
    for _ in range(21):
        last = request(app, "GET", "/api/portable/v1/identity")
    assert last.status_code == 429


def test_slow_drip_body_has_one_total_deadline(tmp_path):
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    async def run():
        scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/portable/v1/pairing/exchange", "query_string": b"",
                 "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 18766),
                 "headers": [(b"host", b"127.0.0.1:18766"), (b"content-type", b"application/json")]}
        chunks = iter((b'{', b'"x"', b':1}'))
        sent = []
        async def receive():
            await asyncio.sleep(2)
            chunk = next(chunks)
            return {"type": "http.request", "body": chunk, "more_body": chunk != b':1}'}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        assert sent[0]["status"] == 408
    asyncio.run(run())


def test_shared_golden_fixture_signature_and_pin():
    from pathlib import Path
    import hashlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    fixture = json.loads((Path(__file__).parent / "fixtures" / "mobile_pairing_golden.json").read_text(encoding="utf8"))
    decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    envelope = fixture["identity"]
    public = decode(envelope["public_key"])
    assert hashlib.sha256(public).hexdigest() == fixture["bundle"]["public_key_pin"]
    key = serialization.load_der_public_key(public)
    raw = decode(envelope["payload"])
    key.verify(decode(envelope["signature"]), raw, ec.ECDSA(hashes.SHA256()))
    assert json.loads(raw) == fixture["descriptor_payload"]
