"""The HTTP/WS boundary uses in-process ASGI only; no app startup or sockets."""
import asyncio
from http.cookies import SimpleCookie

import httpx
import pytest
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from api_security import ApiSecurity, SecuritySettings


PASSWORD = "test-only-password"
SECRET = "test-only-signing-secret-32-bytes-long"


def boundary(**overrides):
    values = dict(password=PASSWORD, signing_secret=SECRET,
                  allowed_hosts=("localhost", "127.0.0.1", "::1", "radio.test"))
    values.update(overrides)
    security = ApiSecurity(SecuritySettings(**values))
    app = FastAPI()
    security.install(app)

    @app.get("/api/private")
    @app.post("/api/private")
    @app.get("/media/song.mp3")
    async def private():
        return {"private": True}

    return security, app


def request(app, method="GET", path="/api/private", *, remote=True, headers=None,
            json=None, cookie=None, scheme="http"):
    async def run():
        transport = httpx.ASGITransport(app=app, client=("192.0.2.8" if remote else "127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url=f"{scheme}://radio.test") as client:
            merged = list(headers.items()) if isinstance(headers, dict) else list(headers or [])
            if cookie:
                merged.append(("Cookie", cookie))
            return await client.request(method, path, headers=merged, json=json)
    return asyncio.run(run())


def login(app, **kwargs):
    response = request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                       json={"password": PASSWORD}, **kwargs)
    assert response.status_code == 200
    return response.headers["set-cookie"].split(";", 1)[0]


def test_loopback_native_and_vite_work_but_opaque_and_drive_by_origins_do_not():
    _, app = boundary(password="", signing_secret="")
    for headers in ({}, {"Origin": "http://localhost:3000"},
                    {"Origin": "http://127.0.0.1:3000"}):
        assert request(app, "POST", remote=False, headers=headers).status_code == 200
    assert request(app, remote=False, headers={"Origin": "https://evil.test"}).status_code == 403
    assert request(app, "POST", remote=False, headers={"Origin": "null"}).status_code == 403
    assert request(app, remote=False, headers={"Host": "evil.test"}).status_code == 400


@pytest.mark.parametrize("path", ["/api/private", "/media/song.mp3"])
def test_originless_cross_site_browser_fetch_cannot_use_loopback_trust(path):
    _, app = boundary(password="", signing_secret="")
    assert request(app, path=path, remote=False,
                   headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert request(app, path=path, remote=False,
                   headers=[("Sec-Fetch-Site", "same-origin"),
                            ("Sec-Fetch-Site", "cross-site")]).status_code == 403
    assert request(app, path=path, remote=False).status_code == 200
    assert request(app, path=path, remote=False,
                   headers={"Origin": "http://localhost:3000", "Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_originless_cross_site_websocket_cannot_use_loopback_trust():
    security, _ = boundary(password="", signing_secret="")

    async def check(headers):
        sent = []
        scope = {"type": "websocket", "scheme": "ws", "path": "/ws/events", "query_string": b"",
                 "headers": [(b"host", b"radio.test"), *headers],
                 "client": ("127.0.0.1", 12345), "server": ("radio.test", 80)}
        async def receive():
            return {"type": "websocket.connect"}
        async def send(message):
            sent.append(message)
        return await security.authorize_websocket(WebSocket(scope, receive, send)), sent

    for headers, expected in [
        ([(b"sec-fetch-site", b"cross-site")], False),
        ([(b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"cross-site")], False),
        ([], True),
        ([(b"origin", b"http://localhost:3000"), (b"sec-fetch-site", b"cross-site")], True),
    ]:
        allowed, sent = asyncio.run(check(headers))
        assert allowed is expected
        assert sent == ([] if expected else [
            {"type": "websocket.close", "code": 1008, "reason": "Access denied"}])


@pytest.mark.parametrize("path", ["/api/private", "/media/song.mp3", "/docs", "/openapi.json"])
def test_remote_cannot_read_without_authentication(path):
    _, app = boundary()
    assert request(app, path=path).status_code == 401


def test_remote_requires_configuration_even_if_client_spoofs_forwarding_headers():
    _, app = boundary(password="", signing_secret="")
    assert request(app, headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401
    assert request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                   json={"password": PASSWORD}).status_code == 503


def test_configured_password_disables_loopback_bypass_for_reverse_proxy_safety():
    _, app = boundary()
    assert request(app, remote=False).status_code == 401
    cookie = login(app)
    assert request(app, remote=False, cookie=cookie).status_code == 200
    with pytest.raises(ValueError, match="loopback"):
        boundary(allow_loopback_without_auth=True)


def test_cookie_authenticates_media_without_origin_and_logout_revokes_cookie():
    _, app = boundary()
    cookie = login(app)
    assert request(app, path="/media/song.mp3", cookie=cookie).status_code == 200
    status = request(app, path="/api/auth/status", cookie=cookie).json()
    assert status["authenticated"] is True
    assert request(app, "POST", cookie=cookie).status_code == 403
    assert request(app, "POST", cookie=cookie, headers={"Origin": "http://radio.test"}).status_code == 200
    assert request(app, "POST", "/api/auth/logout", cookie=cookie,
                   headers={"Origin": "http://radio.test"}).status_code == 200
    assert request(app, cookie=cookie).status_code == 401


def test_login_and_cookie_flags_and_csrf():
    _, app = boundary()
    for origin in (None, "null", "https://evil.test", "http://localhost:3000"):
        headers = {} if origin is None else {"Origin": origin}
        assert request(app, "POST", "/api/auth/login", headers=headers,
                       json={"password": PASSWORD}).status_code == 403
    response = request(app, "POST", "/api/auth/login", scheme="https",
                       headers={"Origin": "https://radio.test"}, json={"password": PASSWORD})
    assert response.status_code == 200
    parsed = SimpleCookie(response.headers["set-cookie"])
    morsel = next(iter(parsed.values()))
    assert morsel["httponly"] and morsel["secure"]
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/"
    assert not morsel["domain"]


def test_expiry_tamper_and_bounded_failed_login_attempts():
    security, app = boundary(session_seconds=10, max_login_attempts=2)
    now = [1000.0]
    security._clock = lambda: now[0]
    cookie = login(app)
    assert request(app, cookie=cookie + "tamper").status_code == 401
    now[0] += 11
    assert request(app, cookie=cookie).status_code == 401
    for _ in range(2):
        response = request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                           json={"password": "wrong"})
        assert response.status_code == 401
    assert request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                   json={"password": PASSWORD}).status_code == 429
    now[0] += 301
    assert login(app)


def test_remote_and_cross_origin_websockets_require_valid_session():
    security, app = boundary()
    cookie = login(app)

    async def check(remote, origin, cookie_header=None, host="radio.test"):
        sent = []
        headers = [(b"host", host.encode())]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        if cookie_header:
            headers.append((b"cookie", cookie_header.encode()))
        scope = {"type": "websocket", "scheme": "ws", "path": "/ws/events", "query_string": b"",
                 "headers": headers, "client": ("192.0.2.8" if remote else "127.0.0.1", 12345),
                 "server": ("radio.test", 80)}
        async def receive():
            return {"type": "websocket.connect"}
        async def send(message):
            sent.append(message)
        allowed = await security.authorize_websocket(WebSocket(scope, receive, send))
        return allowed, sent

    for remote, origin, token, host, expected in [
        (True, "http://radio.test", None, "radio.test", False),
        (True, "http://radio.test", cookie, "radio.test", True),
        (True, None, cookie, "radio.test", False),
        (True, "https://evil.test", cookie, "radio.test", False),
        (False, "null", None, "localhost", False),
        (False, None, None, "localhost", False),
        (False, "null", cookie, "localhost", False),
        (False, None, cookie, "localhost", True),
        (False, "https://evil.test", None, "localhost", False),
        (False, None, None, "evil.test", False),
    ]:
        allowed, sent = asyncio.run(check(remote, origin, token, host))
        assert allowed is expected
        if not expected:
            assert sent == [{"type": "websocket.close", "code": 1008, "reason": "Access denied"}]


def test_duplicate_host_origin_and_oversized_login_body_fail_closed():
    _, app = boundary()
    assert request(app, headers={"Host": "radio.test:invalid"}).status_code == 400
    assert request(app, headers=[("Host", "radio.test"), ("Host", "localhost")]).status_code == 400
    assert request(app, headers=[("Origin", "http://radio.test"),
                                 ("Origin", "https://evil.test")]).status_code == 403
    assert request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                   json={"password": "x" * 5000}).status_code == 413


def test_session_limit_revokes_oldest_and_malformed_password_is_rejected():
    _, app = boundary(max_sessions=1)
    first = login(app)
    second = login(app)
    assert request(app, cookie=first).status_code == 401
    assert request(app, cookie=second).status_code == 200
    assert request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                   json={"password": "\ud800"}).status_code == 400


def test_login_client_tracking_is_bounded():
    security, app = boundary(max_login_clients=1)
    response = request(app, "POST", "/api/auth/login", headers={"Origin": "http://radio.test"},
                       json={"password": "wrong"})
    assert response.status_code == 401
    assert not security._take_login_attempt({"client": ("192.0.2.9", 9999)})
    assert len(security._attempts) == 1


def test_middleware_protects_websocket_routes_without_explicit_guard():
    _, app = boundary()
    reached = []

    @app.websocket("/ws/events")
    async def events(websocket: WebSocket):
        reached.append(True)
        await websocket.accept()
        await websocket.close()

    async def run():
        sent = []
        scope = {"type": "websocket", "scheme": "ws", "path": "/ws/events", "query_string": b"",
                 "headers": [(b"host", b"radio.test"), (b"origin", b"http://radio.test")],
                 "client": ("192.0.2.8", 12345), "server": ("radio.test", 80)}
        async def receive():
            return {"type": "websocket.connect"}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent

    assert asyncio.run(run()) == [{"type": "websocket.close", "code": 1008, "reason": "Access denied"}]
    assert not reached


def test_short_secret_configuration_fails_closed():
    with pytest.raises(ValueError):
        boundary(signing_secret="too-short")


def test_public_login_ui_is_explicit_and_only_get_head_with_boundary_checks():
    _, app = boundary(public_get_paths=("/", "/assets/"))

    @app.api_route("/", methods=["GET", "HEAD", "POST"])
    @app.api_route("/assets/login.js", methods=["GET", "HEAD", "POST"])
    async def public_bundle():
        return {"bundle": True}

    for path in ("/", "/assets/login.js"):
        for method in ("GET", "HEAD"):
            assert request(app, method, path).status_code == 200
        assert request(app, "POST", path, headers={"Origin": "http://radio.test"}).status_code == 401
        assert request(app, path=path, headers={"Host": "evil.test"}).status_code == 400
        assert request(app, path=path, headers={"Origin": "https://evil.test"}).status_code == 403
    assert request(app).status_code == 401
    assert request(app, path="/media/song.mp3").status_code == 401
    for unsafe in (("/api/",), ("/media/",), ("/", "/"), ("/assets",)):
        with pytest.raises(ValueError):
            boundary(public_get_paths=unsafe)


def test_public_ui_is_private_by_default():
    _, app = boundary()

    @app.get("/")
    async def index():
        return {"bundle": True}

    assert request(app, path="/").status_code == 401


def test_fixed_public_https_origin_works_over_http_upstream_without_forwarded_headers():
    _, app = boundary(public_origin="https://radio.test")
    response = request(app, "POST", "/api/auth/login", scheme="http",
                       headers={"Origin": "https://radio.test"}, json={"password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    assert next(iter(SimpleCookie(response.headers["set-cookie"]).values()))["secure"]
    assert request(app, "POST", cookie=cookie, headers={"Origin": "https://radio.test"}).status_code == 200
    assert request(app, "POST", cookie=cookie, headers={"Origin": "http://radio.test"}).status_code == 403
    assert request(app, "POST", cookie=cookie,
                   headers={"Host": "localhost", "Origin": "https://radio.test"}).status_code == 403
    assert request(app, "POST", cookie=cookie,
                   headers={"Host": "radio.test:8443", "Origin": "https://radio.test"}).status_code == 403
    assert request(app, "POST", cookie=cookie,
                   headers={"Origin": "https://evil.test", "X-Forwarded-Host": "radio.test",
                            "X-Forwarded-Proto": "https"}).status_code == 403


@pytest.mark.parametrize("origin", ["http://radio.test", "https://evil.test", "https://radio.test/",
                                   "https://radio.test/path", "https://radio.test?x=1",
                                   "https://radio.test?", "https://radio.test#",
                                   "https://radio.\ttest", "https://user@radio.test", "null"])
def test_public_origin_configuration_is_exact_and_https(origin):
    with pytest.raises(ValueError):
        boundary(public_origin=origin)


@pytest.mark.parametrize("invalidate", ["expire", "logout"])
@pytest.mark.parametrize("direction", ["outbound", "inbound"])
def test_open_websocket_stops_data_after_session_expiry_or_logout(invalidate, direction):
    security, app = boundary(session_seconds=10)
    now = [1000.0]
    security._clock = lambda: now[0]
    cookie = login(app)
    received = []

    async def expire_session():
        if invalidate == "expire":
            now[0] += 11
        else:
            transport = httpx.ASGITransport(app=app, client=("192.0.2.8", 12345))
            async with httpx.AsyncClient(transport=transport, base_url="http://radio.test") as client:
                response = await client.post("/api/auth/logout", headers={
                    "Origin": "http://radio.test", "Cookie": cookie})
                assert response.status_code == 200

    @app.websocket("/ws/lifetime")
    async def lifetime(websocket: WebSocket):
        await websocket.accept()
        if direction == "outbound":
            await expire_session()
            for _ in range(2):
                try:
                    await websocket.send_text("private broadcast")
                except WebSocketDisconnect:
                    pass
        else:
            # Invalidation happens during the receive await, after its initial
            # authentication check, so a wrapper must check again afterwards.
            try:
                received.append(await websocket.receive_text())
            except WebSocketDisconnect as exc:
                received.append(exc.code)
        try:
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def run():
        sent = []
        connected = False
        scope = {"type": "websocket", "scheme": "ws", "path": "/ws/lifetime", "query_string": b"",
                 "headers": [(b"host", b"radio.test"), (b"origin", b"http://radio.test"),
                             (b"cookie", cookie.encode())],
                 "client": ("192.0.2.8", 12345), "server": ("radio.test", 80)}
        async def receive():
            nonlocal connected
            if not connected:
                connected = True
                return {"type": "websocket.connect"}
            await expire_session()
            return {"type": "websocket.receive", "text": "private command"}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent

    sent = asyncio.run(run())
    assert sent == [{"type": "websocket.accept", "subprotocol": None, "headers": []},
                    {"type": "websocket.close", "code": 1008, "reason": "Access denied"}]
    assert received == ([1008] if direction == "inbound" else [])


def test_client_mount_must_not_shadow_the_auth_router(tmp_path):
    """main.py installs the boundary before mounting the built client at "/".

    Starlette matches routes in registration order, so a Mount("/") registered
    first answers /api/auth/* itself with 404 and the client can never discover
    whether a password is configured. Private routes stay hidden behind the 401
    gate, so only the auth endpoints reveal a reversed registration order.
    """
    (tmp_path / "index.html").write_text("<html>Radio</html>", encoding="utf-8")
    _, app = boundary(password="", signing_secret="")
    app.mount("/", StaticFiles(directory=str(tmp_path), html=True), name="radio-client")

    status = request(app, path="/api/auth/status", remote=False)
    assert status.status_code == 200
    assert status.json() == {"authenticated": True, "configured": False}
    assert request(app, path="/", remote=False).text == "<html>Radio</html>"
