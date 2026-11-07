"""Gateway routes for connected recommendations: allow-list, scope, limits, ingress, admin."""
import json
import secrets
import time

import pytest
from fastapi import FastAPI

from mobile_gateway import create_mobile_gateway
from mobile_hosting import MobileHosting, create_mobile_hosting_router
from mobile_recommendations import MobileRecommendations, availability_digest
from portable_sync import PortableProfileStore
from tests.test_mobile_pairing import Protector, request, setup
from tests.test_mobile_hosting import Listener
from tests.test_recommendation_service import TOTAL, library  # noqa: F401  (fixture)
from db import database

PREFIX = "/api/portable/v1"
IDS = [str(i) for i in range(10, TOTAL + 1, 10)]
JSON = {"Content-Type": "application/json"}


def dump(value):
    return json.dumps(value).encode()


class Rig:
    def __init__(self, tmp_path, library, monkeypatch):
        database.set_setting("llm_reranking_enabled", "false")
        self.store, identity, pairing = setup(tmp_path)
        from engine import taste
        memory = taste.MemorySystem()
        monkeypatch.setattr(memory, "_batch_get_chroma_embeddings",
                            lambda ids, name="songs": {str(i): library[name].vectors[int(i)] for i in ids if int(i) in library[name].vectors})
        monkeypatch.setattr(memory, "_get_chroma_embedding", lambda sid, name="songs": library[name].vectors.get(int(sid)))
        self.service = MobileRecommendations(self.store, memory=memory, deadline_seconds=5)
        self.app = create_mobile_gateway(self.store, identity, pairing, recommendations=self.service)
        self.app.state.admission.enable(1)
        self.device = self.store.issue_device("Phone", ["catalog:read", "recommend:read"])
        self.headers = {"Authorization": "Bearer " + self.device["token"], **JSON}

    def call(self, method, path, body=None, headers=None, raw=None):
        content = raw if raw is not None else (None if body is None else dump(body))
        return request(self.app, method, path, self.headers if headers is None else headers, content)

    def availability(self, ids=IDS, revision=12):
        return self.call("POST", PREFIX + "/availability", {
            "protocol_version": 1, "catalog_id": self.store.catalog_id, "catalog_revision": revision,
            "song_ids": ids, "digest": availability_digest(ids)})

    def context(self, **overrides):
        body = {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None, "recent": []}
        body.update(overrides)
        return self.call("POST", PREFIX + "/contexts", body)

    def recommend(self, context, digest, epoch=1, revision=12, request_id="request-1", **overrides):
        body = {"protocol_version": 1, "request_id": request_id, "epoch": epoch, "availability_digest": digest,
                "catalog_revision": revision, "anchor_song_id": "5", "exclude_song_ids": ["10"]}
        body.update(overrides)
        return self.call("POST", f"{PREFIX}/contexts/{context}/recommendations", body)


@pytest.fixture
def rig(tmp_path, library, monkeypatch):
    return Rig(tmp_path, library, monkeypatch)


def test_capabilities_advertise_connected_recommendations_and_scope(rig):
    result = rig.call("GET", PREFIX + "/capabilities").json()
    assert result["connected_recommendations"] is True
    assert result["scopes"] == ["catalog:read", "recommend:read"]


def test_full_usb_flow_through_the_gateway(rig):
    uploaded = rig.availability()
    assert uploaded.status_code == 200
    assert uploaded.headers["cache-control"] == "no-store"
    assert uploaded.json()["count"] == len(IDS)
    created = rig.context()
    assert created.status_code == 200 and created.json()["epoch"] == 1
    context = created.json()["context_id"]
    updated = rig.call("POST", f"{PREFIX}/contexts/{context}", {
        "protocol_version": 1, "mood": "focus", "exploration": .5, "vibe_lock_song_id": None,
        "recent": [{"song_id": "20", "outcome": "completed"}]})
    assert updated.status_code == 200 and updated.json()["epoch"] == 2
    reply = rig.recommend(context, uploaded.json()["digest"], epoch=2)
    assert reply.status_code == 200
    value = reply.json()
    assert value["status"] == "ok" and value["context_id"] == context and value["epoch"] == 2
    assert len(value["picks"]) == 5 and all(p["song_id"] in IDS and p["song_id"] != "10" for p in value["picks"])
    assert reply.headers["cache-control"] == "no-store"


def test_stale_epoch_digest_and_unknown_context_use_the_frozen_error_shapes(rig):
    digest = rig.availability().json()["digest"]
    context = rig.context().json()["context_id"]
    stale = rig.recommend(context, digest, epoch=9)
    assert (stale.status_code, stale.json()) == (409, {"detail": {"reason": "epoch_stale"}})
    stale = rig.recommend(context, "0" * 64)
    assert (stale.status_code, stale.json()) == (409, {"detail": {"reason": "availability_stale"}})
    missing = rig.recommend("m" * 24, digest)
    assert (missing.status_code, missing.json()) == (404, {"detail": "context_not_found"})
    assert rig.availability(ids=["10", "9999"]).status_code == 400


def test_another_devices_context_is_hidden(rig):
    rig.availability()
    context = rig.context().json()["context_id"]
    other = rig.store.issue_device("Tablet", ["recommend:read"])
    headers = {"Authorization": "Bearer " + other["token"], **JSON}
    digest = availability_digest(IDS)
    for path, body in ((f"{PREFIX}/contexts/{context}", {"protocol_version": 1, "mood": "chill", "exploration": .1,
                                                          "vibe_lock_song_id": None, "recent": []}),
                       (f"{PREFIX}/contexts/{context}/recommendations", {
                           "protocol_version": 1, "request_id": "r", "epoch": 1, "availability_digest": digest,
                           "catalog_revision": 12, "anchor_song_id": "5", "exclude_song_ids": []})):
        answer = rig.call("POST", path, body, headers)
        assert (answer.status_code, answer.json()) == (404, {"detail": "context_not_found"})


def test_authentication_and_scope_enforcement(rig):
    body = {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None, "recent": []}
    paths = [PREFIX + "/availability", PREFIX + "/contexts", PREFIX + "/contexts/" + "c" * 24,
             PREFIX + "/contexts/" + "c" * 24 + "/recommendations"]
    wrong = rig.store.issue_device("Sync only", ["catalog:read", "profile:read", "profile:write"])
    expired = rig.store.issue_device("Old", ["recommend:read"], lifetime=-5)
    revoked = rig.store.issue_device("Gone", ["recommend:read"])
    rig.store.revoke_device(revoked["device_id"])
    for path in paths:
        assert rig.call("POST", path, body, JSON).status_code == 401                       # missing
        assert rig.call("POST", path, body, {**JSON, "Authorization": "Basic abc"}).status_code == 401
        assert rig.call("POST", path, body, {**JSON, "Authorization": "Bearer " + "x" * 43}).status_code == 401
        for device in (expired, revoked):
            answer = rig.call("POST", path, body, {**JSON, "Authorization": "Bearer " + device["token"]})
            assert answer.status_code == 401
        denied = rig.call("POST", path, body, {**JSON, "Authorization": "Bearer " + wrong["token"]})
        assert (denied.status_code, denied.json()) == (403, {"detail": "Device token lacks recommend:read scope"})


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_other_methods_are_not_routes(rig, method):
    context = "c" * 24
    for path in (PREFIX + "/availability", PREFIX + "/contexts", f"{PREFIX}/contexts/{context}",
                 f"{PREFIX}/contexts/{context}/recommendations"):
        assert rig.call(method, path, None).status_code == 404


@pytest.mark.parametrize("path", [
    "/availability/", "/contexts/", "/availability?x=1", "/contexts?x=1", "/contexts/short",
    "/contexts/" + "c" * 65, "/contexts/" + "c" * 24 + "/recommendations/", "/contexts/" + "c" * 24 + "/other",
    "/contexts/" + "c" * 24 + "/recommendations?x=1", "/contexts/" + "c" * 23 + "!", "/contexts/" + "c" * 24 + "/../x",
    "//availability", "/Availability", "/contexts/%63%63%63%63%63%63%63%63%63%63%63%63%63%63%63%63",
    "/contexts/" + "c" * 24 + "/recommendations/extra", "/commits",
])
def test_path_variants_are_rejected_before_authentication(rig, path):
    assert rig.call("POST", PREFIX + path, {"protocol_version": 1}, {**JSON}).status_code == 404
    assert rig.call("POST", PREFIX + path, {"protocol_version": 1}).status_code == 404


def test_context_id_length_bounds_route(rig):
    for size, expected in ((15, 404), (16, 404), (64, 404)):                      # valid shape, unknown context
        answer = rig.call("POST", f"{PREFIX}/contexts/{'a' * size}", {"protocol_version": 1, "mood": "neutral",
                          "exploration": .3, "vibe_lock_song_id": None, "recent": []})
        assert answer.status_code == expected
        assert answer.json()["detail"] == ("Mobile route not found" if size == 15 else "context_not_found")


@pytest.mark.parametrize("path,limit", [("/availability", 256 * 1024), ("/contexts", 8 * 1024),
                                        ("/contexts/" + "c" * 24, 8 * 1024),
                                        ("/contexts/" + "c" * 24 + "/recommendations", 16 * 1024)])
def test_per_route_body_limits(rig, path, limit):
    filler = b" " * limit
    assert rig.call("POST", PREFIX + path, raw=b'{"protocol_version":1}' + filler).status_code == 413
    # exactly at the limit passes the gateway (the service then rejects the shape)
    within = b'{"protocol_version":1}' + b" " * (limit - len(b'{"protocol_version":1}'))
    assert len(within) == limit
    assert rig.call("POST", PREFIX + path, raw=within).status_code in (404, 422)


def test_framing_content_type_and_json_strictness(rig):
    path = PREFIX + "/contexts"
    assert rig.call("POST", path, {"protocol_version": 1}, {"Authorization": rig.headers["Authorization"]}).status_code == 415
    assert rig.call("POST", path, raw=b"{").status_code == 422
    assert rig.call("POST", path, raw=b'{"protocol_version":1,"protocol_version":1}').status_code == 422
    assert rig.call("POST", path, raw=b"[]").status_code == 422
    assert rig.call("POST", path, raw=b'{"protocol_version":1,"mood":"neutral","exploration":NaN,"vibe_lock_song_id":null,"recent":[]}').status_code == 422
    assert rig.call("POST", path, {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None,
                                   "recent": [], "unknown": True}).status_code == 422


def test_usb_disabled_and_remote_ingress(rig):
    rig.availability()
    assert rig.context().status_code == 200
    # Remote ingress needs an admitted Quick Tunnel endpoint plus the edge headers.
    admission = rig.app.state.admission
    host = "quiet-otter.trycloudflare.com"
    remote = {**rig.headers, "Host": host, "X-Forwarded-Proto": "https", "CF-Connecting-IP": "203.0.113.9"}
    body = {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None, "recent": []}
    assert rig.call("POST", PREFIX + "/contexts", body, remote).status_code == 503       # tunnel not admitted
    admission.enable_remote("https://" + host, 1, admission.monotonic())
    answer = rig.call("POST", PREFIX + "/contexts", body, remote)
    assert answer.status_code == 200 and answer.json()["epoch"] == 1
    digest = availability_digest(IDS)
    reply = rig.call("POST", f"{PREFIX}/contexts/{answer.json()['context_id']}/recommendations", {
        "protocol_version": 1, "request_id": "r", "epoch": 1, "availability_digest": digest,
        "catalog_revision": 12, "anchor_song_id": "5", "exclude_song_ids": []}, remote)
    assert reply.status_code == 200 and reply.json()["status"] == "ok"
    assert rig.call("POST", PREFIX + "/contexts", body, {**remote, "X-Forwarded-Proto": "http"}).status_code == 400
    admission.disable_remote()
    assert rig.call("POST", PREFIX + "/contexts", body, remote).status_code == 503
    admission.disable()                                                                   # USB closed too
    assert rig.context().status_code == 503


def test_long_ranking_is_not_cut_short_by_the_gateway(rig):
    digest = rig.availability().json()["digest"]
    context = rig.context().json()["context_id"]
    real = rig.service.recommend_fn

    def slow(inputs):
        time.sleep(5.6)          # beyond the gateway's 5 s body-read timeout and any default client deadline
        return real(inputs)
    rig.service.recommend_fn = slow
    started = time.monotonic()
    answer = rig.recommend(context, digest)
    assert answer.status_code == 200 and time.monotonic() - started >= 5.5
    assert answer.json()["status"] == "ok"


# --- admin ---------------------------------------------------------------

def hosting_rig(tmp_path):
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability,
                            protector=Protector(), listener_factory=Listener)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    return store, hosting, app, {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}


def test_admin_grant_and_revoke_connected_radio(tmp_path):
    store, hosting, app, headers = hosting_rig(tmp_path)
    device = store.issue_device("Phone", ["catalog:read"])
    path = "/api/mobile-hosting/devices/" + device["device_id"] + "/connected-radio"
    assert request(app, "POST", path).status_code == 403                  # owner capability required
    assert request(app, "DELETE", path).status_code == 403
    granted = request(app, "POST", path, headers)
    assert granted.status_code == 200
    assert granted.json() == {"device_id": device["device_id"], "scopes": ["catalog:read", "recommend:read"]}
    assert store.authorize(device["token"])["device_id"] == device["device_id"]    # same credential
    assert hosting.status()["enabled"]
    listed = request(app, "GET", "/api/mobile-hosting/devices", headers).json()["devices"]
    assert listed[0]["scopes"] == ["catalog:read", "recommend:read"] and "token" not in listed[0]
    assert request(app, "POST", path, headers).json()["scopes"] == ["catalog:read", "recommend:read"]   # idempotent
    removed = request(app, "DELETE", path, headers)
    assert removed.status_code == 200
    assert removed.json() == {"device_id": device["device_id"], "scopes": ["catalog:read"]}
    assert store.authorize(device["token"])["scopes"] == ["catalog:read"]
    assert request(app, "GET", "/api/mobile-hosting/devices", headers).json()["devices"][0]["scopes"] == ["catalog:read"]


def test_admin_rejects_unknown_revoked_or_expired_devices_and_unsupported_methods(tmp_path):
    store, hosting, app, headers = hosting_rig(tmp_path)
    base = "/api/mobile-hosting/devices/"
    assert request(app, "POST", base + "missing/connected-radio", headers).status_code == 404
    assert request(app, "DELETE", base + "missing/connected-radio", headers).status_code == 404
    revoked = store.issue_device("Gone", ["catalog:read"])
    store.revoke_device(revoked["device_id"])
    expired = store.issue_device("Old", ["catalog:read"], lifetime=-5)
    for device in (revoked, expired):
        assert request(app, "POST", base + device["device_id"] + "/connected-radio", headers).status_code == 404
    live = store.issue_device("Phone", ["catalog:read"])
    for method in ("GET", "PUT", "PATCH"):
        assert request(app, method, base + live["device_id"] + "/connected-radio", headers).status_code == 405


def test_revoking_the_scope_blocks_the_gateway_and_drops_in_memory_state(tmp_path, library, monkeypatch):
    store, hosting, app, headers = hosting_rig(tmp_path)
    rig = Rig(tmp_path / "gw", library, monkeypatch)
    hosting.gateway = rig.app                       # the listener's gateway
    rig.availability()
    context = rig.context().json()["context_id"]
    assert rig.device["device_id"] in rig.service._snapshots
    path = "/api/mobile-hosting/devices/" + rig.device["device_id"] + "/connected-radio"
    assert request(app, "DELETE", path, headers).status_code == 200      # shared DB, same store
    denied = rig.context()
    assert (denied.status_code, denied.json()) == (403, {"detail": "Device token lacks recommend:read scope"})
    assert rig.device["device_id"] not in rig.service._snapshots
    assert context not in rig.service._contexts
    store.grant_connected_radio(rig.device["device_id"])
    assert rig.context().status_code == 200
