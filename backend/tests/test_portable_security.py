import asyncio

import httpx
from fastapi import FastAPI

from api_security import ApiSecurity, SecuritySettings
from portable_api import create_portable_router, native_authorizer
from portable_sync import PortableProfileStore


def setup():
    store = PortableProfileStore()
    app = FastAPI()
    app.include_router(create_portable_router(lambda: store, catalog_service=lambda: None))
    ApiSecurity(SecuritySettings(password="test", signing_secret="s" * 32, allowed_hosts=("radio.test",)),
                native_authorizer=native_authorizer(lambda: store)).install(app)
    return store, app


def request(app, method, path, token=None, body=None, **headers):
    async def run():
        if token:
            headers["Authorization"] = "Bearer " + token
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 123)), base_url="http://radio.test") as client:
            return await client.request(method, path, headers=headers, json=body)
    return asyncio.run(run())


def test_scopes_cannot_escape_to_playback_or_provision_new_tokens():
    store, app = setup()
    token = store.issue_device("phone", ["catalog:read"])["token"]
    assert request(app, "GET", "/api/portable/v1/capabilities", token).status_code == 200
    assert request(app, "GET", "/api/portable/v1/profile", token).status_code == 403
    assert request(app, "POST", "/api/portable/v1/devices", token, {"name": "escalation", "scopes": ["profile:write"]}).status_code != 200
    assert request(app, "GET", "/api/settings", token).status_code == 401


def test_originless_native_write_allowed_but_host_origin_and_revocation_enforced():
    store, app = setup()
    grant = store.issue_device("phone", ["profile:write"])
    event = {"protocol_version": 1, "event_id": "a", "device_id": grant["device_id"], "logical_clock": 1,
             "catalog_id": store.catalog_id,
             "kind": "song_started", "song_id": "1", "attempt_id": "attempt", "occurred_at": 1000}
    path = "/api/portable/v1/events"
    assert request(app, "POST", path, grant["token"], {"protocol_version": 1, "events": [event]}).status_code == 200
    assert request(app, "POST", path, grant["token"], {}, Origin="https://evil.test").status_code == 403
    assert request(app, "GET", "/api/portable/v1/capabilities", grant["token"], Host="evil.test").status_code == 400
    store.revoke_device(grant["device_id"])
    assert request(app, "GET", "/api/portable/v1/capabilities", grant["token"]).status_code == 401


def test_oversized_portable_json_is_rejected_before_event_parsing():
    store, app = setup()
    grant = store.issue_device("phone", ["profile:write"])
    response = request(app, "POST", "/api/portable/v1/events", grant["token"], {"padding": "x" * (2 * 1024 * 1024)})
    assert response.status_code == 413
