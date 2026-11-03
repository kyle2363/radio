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
    headers = {"X-Radio-Mobile-Owner": capability}
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
