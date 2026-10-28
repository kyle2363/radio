"""Single-process API access boundary, independent of radio runtime/storage.

Default unconfigured mode trusts actual loopback peers only. Configuring a password
requires cookies for *all* peers, including a local reverse proxy. Proxy forwarding
headers are never identity: run uvicorn with proxy_headers=False unless its trusted
proxy topology is deliberately configured. Set allowed_hosts to exact public/local
hostnames (no scheme, wildcard, or port). Public TLS should terminate at the ASGI
server or a trusted proxy preserving the HTTPS scheme. Cookies use that scheme to
select Secure. Session/limiter state is bounded, in-memory and per process; use one
worker. Restarting invalidates sessions. No credentials are persisted or logged.

Integration: ApiSecurity(SecuritySettings(...)).install(app). Middleware protects
HTTP and WebSockets; authorize_websocket(websocket) is also available as an explicit
route guard BEFORE accepting. install protects every HTTP route, including
media/docs, except its auth router. Hosts and
origins are checked even for auth routes and trusted loopback peers. CORS remains
a separate browser policy and grants no access. Authentication must wrap CORS if
CORS middleware would otherwise short-circuit OPTIONS before this boundary.
Opaque Origin:null is always rejected: file/data pages and sandboxed attacker
iframes are indistinguishable. Electron renderers must use a trusted HTTP origin.
Native loopback calls without Origin remain supported in local-only mode. Browser
requests omitting Origin must supply Fetch Metadata honestly: Sec-Fetch-Site
cross-site is rejected before loopback trust. This local compatibility mode relies
on modern browser Fetch Metadata; configure a password for stronger isolation.
public_get_paths may expose only the login shell '/' and the '/assets/' bundle
prefix, for GET/HEAD; these routes must serve only the built frontend. Fixed
public_origin supports HTTPS termination without trusting forwarded headers:
the proxy must preserve the public Host, and the configured hostname must be
allowed. It forces Secure cookies; its scheme applies only to matching Hosts.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect


@dataclass(frozen=True)
class SecuritySettings:
    password: str = field(default="", repr=False)
    signing_secret: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    trusted_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    public_get_paths: tuple[str, ...] = ()
    public_origin: str = ""
    allow_loopback_without_auth: bool | None = None
    session_seconds: int = 12 * 60 * 60
    max_login_attempts: int = 5
    login_window_seconds: int = 300
    max_sessions: int = 1024
    max_login_clients: int = 1024
    max_global_login_attempts: int = 100


def _json(detail, status=200):
    return JSONResponse(detail, status_code=status, headers={"Cache-Control": "no-store"})


def _loopback(scope):
    try:
        address = ipaddress.ip_address(scope["client"][0])
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None)
                                           and address.ipv4_mapped.is_loopback)
    except (KeyError, TypeError, ValueError):
        return False


def _header(scope, name):
    values = [v.decode("latin-1") for k, v in scope.get("headers", ()) if k.lower() == name]
    if len(values) > 1:
        raise ValueError("Duplicate security header")
    return values[0] if values else None


def _authority(value):
    if not value or any(c.isspace() for c in value) or any(c in value for c in "/\\?#@,%"):
        raise ValueError("Invalid host")
    parsed = urlsplit("//" + value)
    host, port = parsed.hostname, parsed.port
    if not host or (port is not None and not 1 <= port <= 65535):
        raise ValueError("Invalid host")
    return host.lower(), port


class ApiSecurity:
    cookie_name = "radio_session"

    def __init__(self, settings: SecuritySettings):
        self.settings = settings
        if bool(settings.password) != bool(settings.signing_secret):
            raise ValueError("Password and signing secret must be configured together")
        if settings.signing_secret and len(settings.signing_secret.encode("utf-8")) < 32:
            raise ValueError("Signing secret must contain at least 32 bytes")
        if settings.password and settings.allow_loopback_without_auth:
            raise ValueError("Configured passwords cannot enable loopback authentication bypass")
        for field in ("session_seconds", "max_login_attempts", "login_window_seconds",
                      "max_sessions", "max_login_clients", "max_global_login_attempts"):
            if not isinstance(getattr(settings, field), int) or getattr(settings, field) < 1:
                raise ValueError(f"{field} must be a positive integer")
        hosts = set()
        for entry in settings.allowed_hosts:
            # Bracket bare IPv6 for the URL authority parser.
            candidate = f"[{entry}]" if ":" in entry and not entry.startswith("[") else entry
            host, port = _authority(candidate)
            if port is not None or "*" in host:
                raise ValueError("Allowed hosts must be exact hostnames without ports")
            hosts.add(host)
        if not hosts:
            raise ValueError("At least one allowed host is required")
        self._hosts = hosts
        if (len(set(settings.public_get_paths)) != len(settings.public_get_paths)
                or any(path not in ("/", "/assets/") for path in settings.public_get_paths)):
            raise ValueError("Public paths may contain only '/' and '/assets/' once each")
        self._public_authority = None
        if settings.public_origin:
            origin = settings.public_origin
            parsed = urlsplit(origin)
            if (any(c.isspace() for c in origin) or "?" in origin or "#" in origin
                    or parsed.scheme != "https" or not parsed.netloc
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("Public origin must be an exact HTTPS origin without a path")
            host, port = _authority(parsed.netloc)
            if host not in hosts:
                raise ValueError("Public origin hostname must be an allowed host")
            self._public_authority = (host, port or 443)
        self._secret = settings.signing_secret.encode("utf-8")
        self._password_hash = hashlib.sha256(settings.password.encode("utf-8")).digest()
        self._trust_local = (not settings.password if settings.allow_loopback_without_auth is None
                             else settings.allow_loopback_without_auth)
        self._clock = time.time
        self._lock = threading.Lock()
        self._sessions = OrderedDict()
        self._attempts = OrderedDict()
        self._global_attempts = (0.0, 0)

    @property
    def configured(self):
        return bool(self.settings.password and self._secret)

    def _valid_host(self, scope):
        try:
            host, _ = _authority(_header(scope, b"host"))
            return host in self._hosts
        except (ValueError, TypeError):
            return False

    def _valid_origin(self, scope, *, unsafe=False, websocket=False):
        try:
            origin = _header(scope, b"origin")
            fetch_site = _header(scope, b"sec-fetch-site")
            local = _loopback(scope)
            if origin is None:
                if fetch_site is not None and fetch_site.strip().lower() == "cross-site":
                    return False
                return local or not (unsafe or websocket)
            if origin == "null":
                return False
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.path or parsed.query or parsed.fragment:
                return False
            host, port = _authority(parsed.netloc)
            requested_host, requested_port = _authority(_header(scope, b"host"))
            scheme = {"ws": "http", "wss": "https"}.get(scope["scheme"], scope["scheme"])
            if (requested_host, requested_port or 443) == self._public_authority:
                scheme = "https"
            same_origin = (parsed.scheme == scheme and host == requested_host
                           and (port or (443 if parsed.scheme == "https" else 80))
                           == (requested_port or (443 if scheme == "https" else 80)))
            return same_origin or (local and origin in self.settings.trusted_origins)
        except (ValueError, TypeError, KeyError):
            return False

    def _public_get(self, scope):
        path = scope["path"]
        return scope["method"] in ("GET", "HEAD") and any(
            path == entry if entry == "/" else path.startswith(entry)
            for entry in self.settings.public_get_paths
        )

    def _secure_cookie(self, request):
        return bool(self._public_authority or request.url.scheme == "https")

    def _session_id(self, scope):
        # Starlette's cookie parser is deliberately forgiving. Require one exact
        # named cookie to avoid ambiguous duplicate-cookie interpretation.
        try:
            raw = _header(scope, b"cookie") or ""
            values = [item.strip().partition("=")[2] for item in raw.split(";")
                      if item.strip().partition("=")[0] == self.cookie_name]
            if len(values) != 1 or len(values[0]) > 256:
                return None
            version, expires, session_id, signature = values[0].split(".")
            payload = f"{version}.{expires}.{session_id}"
            expected = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
            if version != "1" or not hmac.compare_digest(signature, expected):
                return None
            expiration = int(expires)
            with self._lock:
                if expiration > self._clock() and self._sessions.get(session_id) == expiration:
                    return session_id
            return None
        except (ValueError, UnicodeError):
            return None

    def authenticated(self, scope):
        return bool((self._trust_local and _loopback(scope)) or
                    (self.configured and self._session_id(scope)))

    async def authorize_websocket(self, websocket: WebSocket):
        if (self._valid_host(websocket.scope) and
                self._valid_origin(websocket.scope, websocket=True) and
                self.authenticated(websocket.scope)):
            return True
        await websocket.close(code=1008, reason="Access denied")
        return False

    def _take_login_attempt(self, scope):
        client = scope.get("client")
        key = client[0] if client else "unknown"
        now = self._clock()
        with self._lock:
            cutoff = now - self.settings.login_window_seconds
            expired = [ip for ip, (started, _) in self._attempts.items() if started <= cutoff]
            for ip in expired:
                del self._attempts[ip]
            started, count = self._global_attempts
            if started <= cutoff:
                started, count = now, 0
            if count >= self.settings.max_global_login_attempts:
                return False
            self._global_attempts = (started, count + 1)
            if key not in self._attempts and len(self._attempts) >= self.settings.max_login_clients:
                return False
            started, count = self._attempts.get(key, (now, 0))
            if count >= self.settings.max_login_attempts:
                return False
            self._attempts[key] = (started, count + 1)
            return True

    async def _login(self, request: Request):
        if not self.configured:
            return _json({"detail": "Remote access is not configured"}, 503)
        if not self._take_login_attempt(request.scope):
            response = _json({"detail": "Too many login attempts"}, 429)
            response.headers["Retry-After"] = str(self.settings.login_window_seconds)
            return response
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                return _json({"detail": "Login body too large"}, 413)
            body.extend(chunk)
        try:
            value = json.loads(body)
            password = value.get("password") if isinstance(value, dict) else None
            if not isinstance(password, str):
                raise ValueError()
            candidate = hashlib.sha256(password.encode("utf-8")).digest()
        except (ValueError, UnicodeError):
            return _json({"detail": "Invalid login request"}, 400)
        if not hmac.compare_digest(candidate, self._password_hash):
            return _json({"detail": "Invalid credentials"}, 401)
        now = self._clock()
        expires = int(now) + self.settings.session_seconds
        session_id = secrets.token_hex(24)
        with self._lock:
            expired = [sid for sid, expiry in self._sessions.items() if expiry <= now]
            for sid in expired:
                del self._sessions[sid]
            while len(self._sessions) >= self.settings.max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = expires
            client = request.scope.get("client")
            self._attempts.pop(client[0] if client else "unknown", None)
        payload = f"1.{expires}.{session_id}"
        signature = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
        response = _json({"authenticated": True})
        response.set_cookie(self.cookie_name, f"{payload}.{signature}", max_age=self.settings.session_seconds,
                            httponly=True, secure=self._secure_cookie(request), samesite="strict", path="/")
        return response

    async def _logout(self, request: Request):
        session_id = self._session_id(request.scope)
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)
        response = _json({"authenticated": False})
        response.delete_cookie(self.cookie_name, path="/", httponly=True,
                               secure=self._secure_cookie(request), samesite="strict")
        return response

    def install(self, app: FastAPI):
        router = APIRouter(prefix="/api/auth", tags=["authentication"])
        router.add_api_route("/login", self._login, methods=["POST"])
        router.add_api_route("/logout", self._logout, methods=["POST"])

        @router.get("/status")
        async def status(request: Request):
            return _json({"authenticated": self.authenticated(request.scope), "configured": self.configured})

        app.include_router(router)
        app.add_middleware(_SecurityMiddleware, security=self)
        return self


class _SecurityMiddleware:
    def __init__(self, app, security: ApiSecurity):
        self.app = app
        self.security = security

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            if await self.security.authorize_websocket(WebSocket(scope, receive, send)):
                await self._websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self.security._valid_host(scope):
            response = _json({"detail": "Invalid Host"}, 400)
        elif not self.security._valid_origin(scope, unsafe=scope["method"] not in ("GET", "HEAD", "OPTIONS")):
            response = _json({"detail": "Invalid request origin"}, 403)
        elif (scope["path"] not in ("/api/auth/login", "/api/auth/logout", "/api/auth/status")
              and not self.security._public_get(scope) and not self.security.authenticated(scope)):
            response = _json({"detail": "Authentication required"}, 401)
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)

    async def _websocket(self, scope, receive, send):
        """Invalidate existing connections before any further private data flows.

        No timer task is needed: an idle socket can remain open, but cannot send
        or receive application messages after cookie expiry or logout. Checking
        after receive also covers revocation while the socket was awaiting data.
        """
        revoked = False

        async def deny():
            nonlocal revoked
            if not revoked:
                # Set before awaiting so concurrent broadcast/receive paths close
                # exactly once and never race an additional private message out.
                revoked = True
                await send({"type": "websocket.close", "code": 1008, "reason": "Access denied"})

        async def guarded_receive():
            if revoked or not self.security.authenticated(scope):
                await deny()
                return {"type": "websocket.disconnect", "code": 1008}
            message = await receive()
            if message["type"] == "websocket.receive" and (revoked or not self.security.authenticated(scope)):
                await deny()
                return {"type": "websocket.disconnect", "code": 1008}
            return message

        async def guarded_send(message):
            if message["type"] in ("websocket.accept", "websocket.send"):
                if revoked or not self.security.authenticated(scope):
                    await deny()
                    raise WebSocketDisconnect(code=1008)
            if message["type"] == "websocket.close" and revoked:
                return
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except WebSocketDisconnect:
            if not revoked:
                raise
