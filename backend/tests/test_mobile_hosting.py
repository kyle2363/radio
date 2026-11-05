import asyncio
import io
import json
import secrets

import pytest
from fastapi import FastAPI

from mobile_hosting import MobileHosting, create_mobile_hosting_router, read_parent_capability
from portable_sync import PortableProfileStore
from tests.test_mobile_pairing import Protector, body, request


class Listener:
    starts, stops = 0, 0
    def __init__(self, app):
        self.app = app
    async def start(self):
        type(self).starts += 1
        assert not self.app.state.admission.enabled
    async def stop(self):
        type(self).stops += 1
        assert not self.app.state.admission.enabled


def test_electron_control_headers_follow_desktop_security_without_bypassing_owner(tmp_path):
    from api_security import ApiSecurity, SecuritySettings
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability,
                            protector=Protector(), listener_factory=Listener)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    ApiSecurity(SecuritySettings(trusted_origins=("http://localhost:8765",))).install(app)
    headers = {"Origin": "http://localhost:8765", "Cookie": "desktop_preference=synthetic",
               "X-Radio-Mobile-Owner": capability}
    assert request(app, "GET", "/api/mobile-hosting/devices", headers).status_code == 200
    assert request(app, "GET", "/api/mobile-hosting/status", headers).status_code == 200
    assert request(app, "GET", "/api/mobile-hosting/status",
                   dict(headers, **{"X-Radio-Mobile-Owner": "wrong"})).status_code == 403
    assert request(app, "GET", "/api/mobile-hosting/status",
                   dict(headers, Origin="https://evil.test")).status_code == 403


def test_parent_pipe_opt_in_bounded_exact_and_cleared():
    capability = secrets.token_urlsafe(32)
    env = {"RADIO_MOBILE_CONTROL_PIPE": "1"}
    line = json.dumps({"protocol_version": 1, "capability": capability}).encode() + b"\n"
    assert read_parent_capability(io.BytesIO(line), env) == capability
    assert not env
    for bad in (b"", b"{}\n", b"x" * 2048, line.rstrip(), line.replace(b'"protocol_version": 1', b'"protocol_version": true'),
                line.replace(b'{', b'{"protocol_version":1,')):
        assert read_parent_capability(io.BytesIO(bad), {"RADIO_MOBILE_CONTROL_PIPE": "1"}) is None
    class NeverRead:
        def readline(self, *args):
            raise AssertionError("Ordinary startup must not read stdin")
    assert read_parent_capability(NeverRead(), {}) is None


def test_listener_opt_in_idempotent_restart_and_ticket_invalidation(tmp_path):
    store = PortableProfileStore()
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=secrets.token_urlsafe(32),
                            protector=Protector(), listener_factory=Listener)
    assert hosting.status()["enabled"] is False
    assert not (tmp_path / "identity").exists()
    async def run():
        await hosting.enable()
        generation = hosting.gateway.state.admission.generation
        first_listener = hosting.listener
        assert await hosting.enable() == hosting.status()
        assert hosting.listener is first_listener
        old = hosting.bundle("Phone")
        current = hosting.bundle("Phone")
        assert old["device_name"] == "Phone"
        with pytest.raises(ValueError):
            hosting.pairing.exchange(body(old))
        exchange = body(current)
        receipt = hosting.pairing.exchange(exchange)
        pending = hosting.bundle("Phone")
        await hosting.disable()
        assert not hosting.gateway.state.admission.enabled
        with pytest.raises(ValueError):
            hosting.pairing.exchange(body(pending))
        assert hosting.pairing.exchange(exchange) == receipt
        await hosting.disable()
        await hosting.enable()
        assert hosting.gateway.state.admission.generation > generation
        assert hosting.identity.server_id == old["server_id"]
        await hosting.disable()
    asyncio.run(run())


def test_start_failure_and_protection_failure_leave_admission_closed(tmp_path):
    store = PortableProfileStore()
    class FailedListener(Listener):
        async def start(self):
            raise OSError("port in use")
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=secrets.token_urlsafe(32),
                            protector=Protector(), listener_factory=FailedListener)
    async def run():
        with pytest.raises(OSError):
            await hosting.enable()
        assert not hosting.status()["enabled"]
        assert not hosting.gateway.state.admission.enabled
    asyncio.run(run())
    class NoProtection(Protector):
        def protect(self, value):
            raise RuntimeError("DPAPI unavailable")
    hosting = MobileHosting(lambda: store, tmp_path / "missing", capability=secrets.token_urlsafe(32),
                            protector=NoProtection(), listener_factory=Listener)
    async def unavailable():
        with pytest.raises(RuntimeError):
            await hosting.enable()
        assert not hosting.status()["enabled"]
    asyncio.run(unavailable())


def test_admin_requires_parent_capability_loopback_and_no_device_token(tmp_path):
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability, protector=Protector(), listener_factory=Listener)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    assert request(app, "GET", "/api/mobile-hosting/status").status_code == 403
    assert request(app, "POST", "/api/mobile-hosting/enable", {"X-Radio-Mobile-Owner": "wrong"}).status_code == 403
    headers = {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}
    assert request(app, "GET", "/api/mobile-hosting/status", headers).status_code == 200
    assert request(app, "POST", "/api/mobile-hosting/enable", headers).status_code == 200
    ticket = request(app, "POST", "/api/mobile-hosting/pairing-tickets", dict(headers, **{"Content-Type": "application/json"}), '{"device_name":"Phone"}')
    assert ticket.status_code == 200
    receipt = hosting.pairing.exchange(body(ticket.json()))
    devices = request(app, "GET", "/api/mobile-hosting/devices", headers).json()["devices"]
    assert devices[0]["device_id"] == receipt["device_id"]
    assert "token" not in devices[0]
    assert request(app, "DELETE", "/api/mobile-hosting/devices/" + receipt["device_id"], headers).status_code == 200
    assert request(app, "GET", "/api/mobile-hosting/devices", headers).json()["devices"][0]["revoked"]
    assert request(app, "POST", "/api/mobile-hosting/disable", headers).status_code == 200


def test_explicit_profile_sync_grant_is_owner_only_and_preserves_existing_credential(tmp_path):
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability, protector=Protector(), listener_factory=Listener)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    device = store.issue_device("Phone", ["catalog:read"])
    path = "/api/mobile-hosting/devices/" + device["device_id"] + "/profile-sync"
    assert request(app, "POST", path).status_code == 403
    headers = {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}
    result = request(app, "POST", path, headers).json()
    assert result == {"device_id": device["device_id"], "scopes": ["catalog:read", "profile:read", "profile:write"]}
    assert store.authorize(device["token"])["device_id"] == device["device_id"]
    assert hosting.status()["enabled"]


def test_list_does_not_initialize_portable_schema(tmp_path):
    from db import database
    def never_initialize():
        raise AssertionError("List must not initialize portable storage")
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(never_initialize, tmp_path / "identity", capability=capability)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    assert request(app, "GET", "/api/mobile-hosting/devices", {"X-Radio-Mobile-Owner": capability}).json() == {"devices": []}
    with database.get_connection_cm() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_meta'").fetchone()


def test_remote_hosting_uses_exact_endpoint_signed_generation_and_monotonic_lease(tmp_path):
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    now = [100.0]
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability,
                            protector=Protector(), listener_factory=Listener, monotonic=lambda: now[0])
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    headers = {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}
    endpoint = "https://quiet-otter-4.trycloudflare.com"

    started = request(app, "POST", "/api/mobile-hosting/remote/start", headers,
                      json.dumps({"endpoint": endpoint}))
    assert started.status_code == 200
    value = started.json()
    assert value["enabled"] is True
    assert value["endpoint"] == endpoint
    assert value["connection_generation"] == 1
    assert value["descriptor"]["payload"]
    assert hosting.gateway.state.admission.remote_endpoint == endpoint

    status = request(app, "GET", "/api/mobile-hosting/remote/status", headers).json()
    assert status["descriptor"] != value["descriptor"]  # fresh issue/expiry window
    assert status["connection_generation"] == value["connection_generation"]

    now[0] = 110
    renewed = request(app, "POST", "/api/mobile-hosting/remote/renew", headers,
                      json.dumps({"connection_generation": 1}))
    assert renewed.status_code == 200
    assert hosting.gateway.state.admission.remote_is_live(now[0])
    now[0] = 126
    assert not hosting.gateway.state.admission.remote_is_live(now[0])

    # USB's separate generation remains unaffected by remote lease expiry.
    assert request(app, "POST", "/api/mobile-hosting/enable", headers).status_code == 200
    assert hosting.gateway.state.admission.usb_enabled
    stopped = request(app, "POST", "/api/mobile-hosting/remote/stop", headers,
                      json.dumps({"connection_generation": 1}))
    assert stopped.status_code == 200
    assert not hosting.gateway.state.admission.remote_is_live(now[0])
    assert hosting.gateway.state.admission.usb_enabled


def test_remote_owner_controls_validate_endpoint_and_fence_stale_generations(tmp_path):
    store = PortableProfileStore()
    capability = secrets.token_urlsafe(32)
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability,
                            protector=Protector(), listener_factory=Listener)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    headers = {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}
    for endpoint in (
        "http://quiet-otter.trycloudflare.com",
        "https://quiet-otter.trycloudflare.com.evil.test",
        "https://quiet-otter.trycloudflare.com:443",
        "https://user@quiet-otter.trycloudflare.com",
        "https://quiet-otter.trycloudflare.com/path",
        "https://quiet-otter.trycloudflare.com?x=1",
        "https://quiet-otter.trycloudflare.com#fragment",
        "https://quiet-otter.trycloudflare.com.",
    ):
        result = request(app, "POST", "/api/mobile-hosting/remote/start", headers,
                         json.dumps({"endpoint": endpoint}))
        assert result.status_code == 422
    assert request(app, "POST", "/api/mobile-hosting/remote/start", {},
                   json.dumps({"endpoint": "https://quiet-otter.trycloudflare.com"})).status_code == 403
    started = request(app, "POST", "/api/mobile-hosting/remote/start", headers,
                      json.dumps({"endpoint": "https://quiet-otter.trycloudflare.com"})).json()
    assert started["connection_generation"] == 1
    assert request(app, "POST", "/api/mobile-hosting/remote/renew", headers,
                   json.dumps({"connection_generation": 99})).status_code == 409
    assert request(app, "POST", "/api/mobile-hosting/remote/stop", headers,
                   json.dumps({"connection_generation": 99})).status_code == 409


def test_gateway_remote_host_is_exact_credentialed_and_endpoint_bound(tmp_path):
    import base64
    store = PortableProfileStore()
    device = store.issue_device("Phone", ["catalog:read"])
    capability = secrets.token_urlsafe(32)
    now = [100.0]
    hosting = MobileHosting(lambda: store, tmp_path / "identity", capability=capability,
                            protector=Protector(), listener_factory=Listener, monotonic=lambda: now[0])
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    owner_headers = {"X-Radio-Mobile-Owner": capability, "Content-Type": "application/json"}
    endpoint = "https://quiet-otter.trycloudflare.com"
    started = request(app, "POST", "/api/mobile-hosting/remote/start", owner_headers,
                      json.dumps({"endpoint": endpoint})).json()
    tunnel_headers = {
        "Host": "quiet-otter.trycloudflare.com",
        "CF-Connecting-IP": "2001:db8::10",
        "CF-EW-Via": "15",
        "CF-IPCountry": "US",
        "CF-Visitor": '{"scheme":"https"}',
        "CF-Ray": "a55646b6ee70dc28-EWR",
        "CF-Warp-Tag-ID": "eee28b62-6eaa-4a2d-9a40-3b81c9a91f8a",
        "CF-Worker": "trycloudflare.com",
        "X-Forwarded-For": "2001:db8::10",
        "X-Forwarded-Proto": "https",
        "Accept-Encoding": "gzip",
    }
    identity = request(hosting.gateway, "GET", "/api/portable/v1/identity", tunnel_headers)
    assert identity.status_code == 200
    payload = json.loads(base64.urlsafe_b64decode(identity.json()["payload"] + "=" * (-len(identity.json()["payload"]) % 4)))
    assert payload["endpoint"] == endpoint
    assert payload["connection_generation"] == started["connection_generation"]
    minimal_headers = {"Host": tunnel_headers["Host"], "CF-Connecting-IP": "2001:db8::10",
                       "X-Forwarded-Proto": "https"}
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity", minimal_headers).status_code == 200
    assert request(hosting.gateway, "GET", "/api/portable/v1/capabilities", tunnel_headers).status_code == 401
    assert request(hosting.gateway, "GET", "/api/portable/v1/capabilities",
                   dict(tunnel_headers, Authorization="Bearer " + device["token"])).json()["remote_hosting"] is True
    assert request(hosting.gateway, "POST", "/api/portable/v1/pairing/exchange", tunnel_headers,
                   json.dumps({"protocol_version": 1})).status_code == 404
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity",
                   dict(tunnel_headers, Host="evil.trycloudflare.com")).status_code == 503
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity",
                   dict(tunnel_headers, **{"X-Forwarded-Host": "quiet-otter.trycloudflare.com"})).status_code == 400
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity",
                   dict(tunnel_headers, **{"CF-Connecting-IP": "not-an-ip"})).status_code == 400
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity",
                   dict(minimal_headers, **{"CF-Warp-Tag-ID": "invalid"})).status_code == 400

    # USB uses its own endpoint and the latest server generation.
    assert request(app, "POST", "/api/mobile-hosting/enable", owner_headers).status_code == 200
    usb = request(hosting.gateway, "GET", "/api/portable/v1/identity").json()
    usb_payload = json.loads(base64.urlsafe_b64decode(usb["payload"] + "=" * (-len(usb["payload"]) % 4)))
    assert usb_payload["endpoint"] == "http://127.0.0.1:18766"
    assert usb_payload["connection_generation"] > payload["connection_generation"]

    now[0] = 116
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity", tunnel_headers).status_code == 503
    replacement = request(app, "POST", "/api/mobile-hosting/remote/start", owner_headers,
                          json.dumps({"endpoint": "https://calm-fox.trycloudflare.com"})).json()
    assert replacement["connection_generation"] > started["connection_generation"]
    assert request(hosting.gateway, "GET", "/api/portable/v1/identity",
                   dict(tunnel_headers, Host="quiet-otter.trycloudflare.com")).status_code == 503
