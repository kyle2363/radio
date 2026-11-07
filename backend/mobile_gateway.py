"""Deliberately separate ASGI surface; never mounts desktop routes."""
import asyncio
from collections import OrderedDict
import ipaddress
import json
import math
import re
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from starlette.responses import JSONResponse, Response

from portable_sync import SyncConflict
from mobile_recommendations import CONTEXT_ID, MobileRecommendations, Rejected
from portable.catalog import canonical_json
from mobile_catalog import MAX_REVISION, MOBILE_BATCH_COUNT, MOBILE_BATCH_BYTES

ENDPOINT = "http://127.0.0.1:18766"
PREFIX = "/api/portable/v1"
ROUTES = {("GET", PREFIX + "/identity"), ("POST", PREFIX + "/pairing/exchange"),
          ("GET", PREFIX + "/capabilities")}
MAX_SYNC_BODY = 2 * 1024 * 1024
# Connected recommendations: every route is POST with a JSON body (the phone's
# transport only sends GET and POST). Per-route request limits.
MAX_AVAILABILITY_BODY = 256 * 1024
MAX_CONTEXT_BODY = 8 * 1024
MAX_RECOMMENDATION_BODY = 16 * 1024
MAX_RECOMMENDATION_RESPONSE = 64 * 1024
CONTEXT_PATH = re.compile(re.escape(PREFIX) + r"/contexts/(" + CONTEXT_ID + r")(/recommendations)?")


class EventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    events: list[dict] = Field(min_length=1, max_length=500)


def sync_query(raw):
    if len(raw) > 64:
        raise ValueError()
    result, seen = {"cursor": 0, "limit": 100}, set()
    for pair in raw.split(b"&") if raw else ():
        match = re.fullmatch(rb"(cursor|limit)=(0|[1-9][0-9]{0,15})", pair)
        if not match or match[1] in seen:
            raise ValueError()
        key, value = match[1].decode("ascii"), int(match[2])
        if (key == "cursor" and value > 2**63 - 1) or (key == "limit" and not 1 <= value <= 500):
            raise ValueError()
        result[key] = value
        seen.add(match[1])
    return result


def strict_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(body.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number")))


def catalog_query(raw):
    """Deliberately smaller than generic URL parsing: two decimal integers only."""
    if len(raw) > 96:
        raise ValueError()
    result = {}
    for field in raw.split(b"&") if raw else ():
        match = re.fullmatch(rb"(revision|base_revision)=([1-9][0-9]{0,18})", field)
        if not match:
            raise ValueError()
        key = match[1].decode("ascii")
        value = int(match[2])
        if key in result or value > MAX_REVISION:
            raise ValueError()
        result[key] = value
    return result


def batch_query(raw):
    if len(raw) > 4500:
        raise ValueError()
    match = re.fullmatch(rb"revision=([1-9][0-9]{0,18})&manifest_sha256=([0-9a-f]{64})&digests=([0-9a-f]{64}(?:,[0-9a-f]{64})*)", raw)
    if not match or int(match[1]) > MAX_REVISION:
        raise ValueError()
    digests = match[3].decode("ascii").split(",")
    if len(digests) > MOBILE_BATCH_COUNT or len(set(digests)) != len(digests):
        raise ValueError()
    return dict(revision=int(match[1]), manifest_sha256=match[2].decode("ascii"), digests=digests)


class Admission:
    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.usb_enabled, self.usb_generation = False, 0
        self.remote_endpoint, self.remote_generation = None, 0
        self.remote_deadline = 0.0

    # Compatibility aliases for the USB listener's existing controls.
    @property
    def enabled(self):
        return self.usb_enabled

    @property
    def generation(self):
        return self.usb_generation

    def enable(self, generation):
        self.usb_generation, self.usb_enabled = generation, True

    def disable(self):
        self.usb_enabled = False

    def enable_remote(self, endpoint, generation, now):
        self.remote_endpoint = endpoint
        self.remote_generation = generation
        self.remote_deadline = now + 15.0

    def renew_remote(self, generation, now):
        if generation != self.remote_generation or not self.remote_is_live(now):
            return False
        self.remote_deadline = now + 15.0
        return True

    def remote_is_live(self, now=None):
        now = self.monotonic() if now is None else now
        return bool(self.remote_endpoint and self.remote_deadline > now)

    def disable_remote(self):
        self.remote_endpoint = None
        self.remote_deadline = 0.0

    def disable_all(self):
        self.disable()
        self.disable_remote()


class GatewayBoundary:
    def __init__(self, app, *, store, admission, catalog=None, recommendations=None):
        self.app, self.store, self.admission = app, store, admission
        self.catalog = catalog
        self.recommendations = recommendations
        self._lock, self._buckets = threading.Lock(), OrderedDict()
        self._global = (0, 0)
        self._device_buckets = OrderedDict()
        self._device_global = {}
        self._slots = threading.BoundedSemaphore(16)

    def _rate(self, client, path):
        """Return 0 for admission, otherwise whole seconds until a window frees."""
        now = time.monotonic()
        def delay(start):
            return min(60, max(1, math.ceil(start + 60 - now)))
        limit = 10 if path.endswith("/exchange") else 20 if path.endswith("/identity") else 120
        with self._lock:
            for key in list(self._buckets):
                if self._buckets[key][0] + 60 <= now:
                    del self._buckets[key]
            start, count = self._global
            if start + 60 <= now:
                start, count = now, 0
            if count >= 1000:
                return delay(start)
            self._global = start, count + 1
            key = (client, path)
            if key not in self._buckets and len(self._buckets) >= 1024:
                return delay(min(window[0] for window in self._buckets.values()))
            start, count = self._buckets.get(key, (now, 0))
            self._buckets[key] = start, count + 1
            return 0 if count < limit else delay(start)

    def _rate_device(self, device_id, path):
        now = time.monotonic()
        def delay(start):
            return min(60, max(1, math.ceil(start + 60 - now)))
        with self._lock:
            for key in list(self._device_buckets):
                if self._device_buckets[key][0] + 60 <= now:
                    del self._device_buckets[key]
            key = (device_id, path)
            if key not in self._device_buckets and len(self._device_buckets) >= 4096:
                return delay(min(window[0] for window in self._device_buckets.values()))
            start, count = self._device_buckets.get(key, (now, 0))
            self._device_buckets[key] = (start, count + 1)
            limit = 60
            return 0 if count < limit else delay(start)

    async def __call__(self, scope, receive, send):
        if not self._slots.acquire(blocking=False):
            if scope["type"] == "http":
                await JSONResponse({"detail": "Mobile gateway busy"}, status_code=503,
                                   headers={"Cache-Control": "no-store", "Retry-After": "1"})(scope, receive, send)
            elif scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            return
        try:
            await self._handle(scope, receive, send)
        finally:
            self._slots.release()

    async def _handle(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)

        async def reject(status, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=status,
                               headers={"Cache-Control": "no-store", **(headers or {})})(scope, receive, send)

        path = scope.get("path", "")
        is_catalog = path == PREFIX + "/catalog" and scope["method"] == "GET"
        is_batch = path == PREFIX + "/vector-batch" and scope["method"] == "GET"
        is_vector = bool(re.fullmatch(re.escape(PREFIX) + r"/vectors/[0-9a-f]{64}", path)) and scope["method"] in ("GET", "HEAD")
        is_sync_events = path == PREFIX + "/events" and scope["method"] in ("GET", "POST")
        is_sync_profile = path == PREFIX + "/profile" and scope["method"] == "GET"
        sync_get = is_sync_events and scope["method"] == "GET"
        context_match = CONTEXT_PATH.fullmatch(path) if scope["method"] == "POST" else None
        is_availability = path == PREFIX + "/availability" and scope["method"] == "POST"
        is_context_create = path == PREFIX + "/contexts" and scope["method"] == "POST"
        is_context_update = bool(context_match) and not context_match[2]
        is_recommendation = bool(context_match) and bool(context_match[2])
        is_connected = self.recommendations is not None and (
            is_availability or is_context_create or is_context_update or is_recommendation)
        # The factory supplies this flag explicitly; middleware's inner app is
        # an exception wrapper rather than the FastAPI instance.
        catalog_enabled = self.catalog is not None
        if (((scope["method"], path) not in ROUTES and not (catalog_enabled and (is_catalog or is_vector or is_batch))
             and not (is_sync_events or is_sync_profile) and not is_connected)
                or (scope.get("query_string") and not (is_catalog or is_batch or sync_get))
                or scope.get("raw_path", path.encode()) != path.encode("ascii", errors="replace")):
            return await reject(404, "Mobile route not found")
        try:
            client = scope["client"][0]
            if not ipaddress.ip_address(client).is_loopback:
                return await reject(403, "Gateway loopback peer required")
            headers = scope.get("headers", ())
            if len(headers) > 64 or sum(len(k) + len(v) for k, v in headers) > 8192:
                return await reject(431, "Headers too large")
            values = {}
            for key, value in headers:
                key = key.lower()
                if key in values:
                    return await reject(400, "Duplicate header")
                values[key] = value.decode("latin1")
            host = values.get(b"host")
            if host == "127.0.0.1:18766":
                if not self.admission.usb_enabled:
                    return await reject(503, "USB gateway disabled")
                if any(k.startswith((b"x-forwarded-", b"cf-")) or k in (b"forwarded", b"x-real-ip", b"transfer-encoding") for k in values):
                    return await reject(400, "Proxy and ambiguous framing headers forbidden")
                scope["mobile_endpoint"] = "http://127.0.0.1:18766"
                scope["mobile_generation"] = max(self.admission.usb_generation, self.admission.remote_generation)
                scope["mobile_ingress"] = "usb"
            elif isinstance(host, str) and host == host.lower() and re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", host):
                endpoint = "https://" + host
                now = self.admission.monotonic()
                if endpoint != self.admission.remote_endpoint or not self.admission.remote_is_live(now):
                    return await reject(503, "Remote gateway unavailable")
                # cloudflared preserves Host and supplies these Cloudflare edge
                # observations. They are only checked for transport shape; no
                # forwarded address is trusted for identity or rate limiting.
                allowed_proxy = {b"x-forwarded-proto", b"x-forwarded-for", b"cf-connecting-ip", b"cf-visitor", b"cf-ray",
                                 b"cf-ew-via", b"cf-ipcountry", b"cf-warp-tag-id", b"cf-worker"}
                forbidden = [k for k in values if (k.startswith(b"x-forwarded-") or k.startswith(b"cf-")) and k not in allowed_proxy]
                if forbidden or values.get(b"x-forwarded-proto") != "https":
                    return await reject(400, "Expected HTTPS Cloudflare tunnel headers")
                try:
                    ipaddress.ip_address(values[b"cf-connecting-ip"])
                    if b"x-forwarded-for" in values:
                        forwarded = values[b"x-forwarded-for"].split(",")
                        if not 1 <= len(forwarded) <= 3 or any(not ipaddress.ip_address(item.strip()) for item in forwarded):
                            raise ValueError()
                    if b"cf-visitor" in values and strict_json(values[b"cf-visitor"].encode("utf8")) != {"scheme": "https"}:
                        raise ValueError()
                    if b"cf-ew-via" in values and not re.fullmatch(r"[0-9]{1,3}", values[b"cf-ew-via"]):
                        raise ValueError()
                    if b"cf-ipcountry" in values and not re.fullmatch(r"[A-Z0-9]{2}", values[b"cf-ipcountry"]):
                        raise ValueError()
                    if b"cf-ray" in values and not re.fullmatch(r"[0-9a-f]{16}-[A-Z0-9]{3,8}", values[b"cf-ray"]):
                        raise ValueError()
                    if b"cf-warp-tag-id" in values:
                        from uuid import UUID
                        warp_id = values[b"cf-warp-tag-id"]
                        if str(UUID(warp_id)) != warp_id:
                            raise ValueError()
                    if b"cf-worker" in values and values[b"cf-worker"] != "trycloudflare.com":
                        raise ValueError()
                except (KeyError, ValueError, UnicodeError):
                    return await reject(400, "Invalid Cloudflare tunnel headers")
                scope["mobile_endpoint"] = endpoint
                scope["mobile_generation"] = self.admission.remote_generation
                scope["mobile_ingress"] = "remote"
            else:
                return await reject(400, "Exact admitted Host required")
            if any(k in values for k in (b"forwarded", b"x-real-ip", b"transfer-encoding", b"content-encoding",
                                         b"expect", b"trailer", b"upgrade")):
                return await reject(400, "Proxy and ambiguous framing headers forbidden")
            if b"range" in values or b"if-range" in values:
                return await reject(400, "Partial resources are not supported")
            if any(k in values for k in (b"origin", b"cookie", b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest")):
                return await reject(403, "Native requests only")
            retry_after = self._rate(client, path)
            if retry_after:
                return await reject(429, "Mobile request limit exceeded", {"Retry-After": str(retry_after)})
            auth = values.get(b"authorization")
            if auth is not None:
                if not auth.startswith("Bearer "):
                    return await reject(401, "Device bearer required")
                identity = self.store.authorize(auth[7:])
                if not identity:
                    return await reject(401, "Device credential invalid, expired or revoked")
                scope["portable_identity"] = identity
                retry_after = self._rate_device(identity["device_id"], path)
                if retry_after:
                    return await reject(429, "Device request limit exceeded", {"Retry-After": str(retry_after)})
            if scope["mobile_ingress"] == "remote" and path == PREFIX + "/pairing/exchange":
                return await reject(404, "Remote pairing is unavailable")
            if (path.endswith("/capabilities") or is_catalog or is_vector or is_batch or is_sync_events or is_sync_profile
                    or is_connected) and not scope.get("portable_identity"):
                return await reject(401, "Device bearer required")
            if is_connected and "recommend:read" not in scope["portable_identity"]["scopes"]:
                return await reject(403, "Device token lacks recommend:read scope")
            if (is_catalog or is_vector or is_batch) and "catalog:read" not in scope["portable_identity"]["scopes"]:
                return await reject(403, "Device token lacks catalog:read scope")
            if is_catalog:
                try:
                    scope["catalog_query"] = catalog_query(scope.get("query_string", b""))
                except ValueError:
                    return await reject(400, "Invalid catalog query")
            if is_batch:
                try:
                    scope["batch_query"] = batch_query(scope.get("query_string", b""))
                except ValueError:
                    return await reject(400, "Invalid vector batch query")
            if sync_get and path.endswith("/events"):
                try:
                    scope["sync_query"] = sync_query(scope.get("query_string", b""))
                except ValueError:
                    return await reject(400, "Invalid synchronization query")
            if is_sync_events and scope["method"] == "GET" and "profile:read" not in scope["portable_identity"]["scopes"]:
                return await reject(403, "Device token lacks profile:read scope")
            if is_sync_profile and "profile:read" not in scope["portable_identity"]["scopes"]:
                return await reject(403, "Device token lacks profile:read scope")
            if is_sync_events and scope["method"] == "POST" and "profile:write" not in scope["portable_identity"]["scopes"]:
                return await reject(403, "Device token lacks profile:write scope")
            length = values.get(b"content-length", "0")
            if not re.fullmatch(r"0|[1-9][0-9]{0,8}", length):
                return await reject(400, "Invalid content length")
            body_limit = (MAX_SYNC_BODY if is_sync_events and scope["method"] == "POST" else
                          MAX_AVAILABILITY_BODY if is_availability else
                          MAX_RECOMMENDATION_BODY if is_recommendation else 8192)
            if is_context_create or is_context_update:
                body_limit = MAX_CONTEXT_BODY
            if int(length) > body_limit:
                return await reject(413, "Mobile body too large")
            if scope["method"] == "POST" and values.get(b"content-type", "").split(";")[0].strip() != "application/json":
                return await reject(415, "JSON required")
            body = bytearray()
            async with asyncio.timeout(5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > body_limit:
                        return await reject(413, "Mobile body too large")
                    if not message.get("more_body"):
                        break
            if (b"content-length" in values and int(length) != len(body)) or (scope["method"] in ("GET", "HEAD") and body):
                return await reject(400, "Unexpected body framing")
        except (ValueError, KeyError, TypeError):
            return await reject(400, "Invalid gateway request")
        except TimeoutError:
            return await reject(408, "Mobile request timed out")

        # A stop, replacement, or owner-lease expiry while a body was arriving
        # fences dispatch of the already-buffered request.
        if scope["mobile_ingress"] == "usb":
            current = self.admission.usb_enabled and self.admission.usb_generation == scope["mobile_generation"]
        else:
            current = (self.admission.remote_endpoint == scope["mobile_endpoint"]
                       and self.admission.remote_generation == scope["mobile_generation"]
                       and self.admission.remote_is_live())
        if not current:
            return await reject(503, "Gateway admission changed during request")

        async def buffered():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        response_limit = (16 * 1024 * 1024 if is_catalog else 8_000_000 if is_batch else
                          MAX_SYNC_BODY if is_sync_events else 262_144 if is_vector else
                          MAX_RECOMMENDATION_RESPONSE if is_connected else 8192)
        messages, response_size = [], 0
        async def capture(message):
            nonlocal response_size
            if message["type"] == "http.response.body":
                response_size += len(message.get("body", b""))
                if response_size > response_limit:
                    raise OverflowError()
            messages.append(message)
        try:
            await self.app(scope, buffered, capture)
        except OverflowError:
            return await reject(502, "Mobile response exceeded route limit")
        for message in messages:
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message["headers"] if k.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)


def create_mobile_gateway(store, identity, pairing, *, catalog=None, monotonic=time.monotonic, recommendations=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    admission = app.state.admission = Admission(monotonic=monotonic)
    recommendations = app.state.recommendations = recommendations or MobileRecommendations(store)

    @app.get(PREFIX + "/identity")
    def descriptor(request: Request):
        return identity.descriptor(request.scope["mobile_endpoint"], request.scope["mobile_generation"])

    @app.post(PREFIX + "/pairing/exchange")
    async def exchange(request: Request):
        try:
            body = strict_json(await request.body())
            return await asyncio.to_thread(pairing.exchange, body)
        except SyncConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid, expired or revoked pairing exchange") from None

    @app.get(PREFIX + "/capabilities")
    def capabilities(request: Request):
        return {"protocol_version": 1, "server_id": identity.server_id,
                "authenticated_device_id": request.scope["portable_identity"]["device_id"],
                "scopes": sorted(request.scope["portable_identity"]["scopes"]),
                "catalog_id": store.catalog_id, "pairing": True,
                "catalog_import": catalog is not None, "catalog_schema": 1,
                "catalog_revision": catalog.status()["revision"] if catalog else None,
                "profile_sync": True, "remote_hosting": True, "connected_recommendations": True, "connected_tuning": True,
                "offline_recommendations": False, "native_speech_generation": False,
                "local_music_required": True}

    @app.get(PREFIX + "/events")
    def sync_events(request: Request):
        try:
            store.capture_desktop()
            return store.events(**request.scope["sync_query"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get(PREFIX + "/profile")
    def sync_profile():
        try:
            store.capture_desktop()
            return store.profile()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post(PREFIX + "/events")
    async def import_sync_events(request: Request):
        try:
            parsed = EventsRequest.model_validate(strict_json(await request.body()))
        except (ValueError, UnicodeError, ValidationError):
            raise HTTPException(422, "Invalid synchronization request") from None
        if parsed.protocol_version != 1:
            raise HTTPException(400, "Unsupported synchronization protocol")
        device_id = request.scope["portable_identity"]["device_id"]
        try:
            store.capture_desktop()
            return store.import_events(device_id, parsed.events)
        except SyncConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    async def connected(request: Request, call, *args):
        try:
            body = strict_json(await request.body())
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Invalid request") from None
        device_id = request.scope["portable_identity"]["device_id"]
        try:
            # Ranking can legitimately take up to the 60 s server deadline; it
            # runs in a worker thread so the gateway's event loop stays free.
            return await asyncio.to_thread(call, device_id, *args, body)
        except Rejected as exc:
            raise HTTPException(exc.status, exc.detail) from None

    @app.post(PREFIX + "/availability")
    async def put_availability(request: Request):
        return await connected(request, recommendations.put_availability)

    @app.post(PREFIX + "/contexts")
    async def create_context(request: Request):
        return await connected(request, recommendations.create_context)

    @app.post(PREFIX + "/contexts/{context_id}")
    async def update_context(context_id: str, request: Request):
        return await connected(request, recommendations.update_context, context_id)

    @app.post(PREFIX + "/contexts/{context_id}/recommendations")
    async def recommend(context_id: str, request: Request):
        return await connected(request, recommendations.recommend, context_id)

    if catalog is not None:
        @app.get(PREFIX + "/catalog")
        def prepared_manifest(request: Request):
            try:
                manifest = catalog.manifest(**request.scope["catalog_query"])
                return Response(canonical_json(manifest), media_type="application/json",
                                headers={"ETag": '"' + manifest["manifest_sha256"] + '"', "X-Radio-Catalog-Batches": "1"})
            except KeyError:
                if catalog.status()["revision"] is None:
                    raise HTTPException(409, "catalog_not_prepared") from None
                raise HTTPException(404, "Prepared revision not found") from None
            except (ValueError, OSError):
                raise HTTPException(400, "Prepared catalog unavailable or invalid base") from None

        @app.get(PREFIX + "/vector-batch")
        def prepared_batch(request: Request):
            try:
                data = catalog.batch(**request.scope["batch_query"])
                if len(data) > MOBILE_BATCH_BYTES:
                    raise ValueError("Batch exceeds limit")
                return Response(data, media_type="application/octet-stream")
            except KeyError:
                raise HTTPException(404, "Prepared batch revision not found") from None
            except ValueError:
                raise HTTPException(400, "Invalid prepared vector batch") from None
            except OSError:
                raise HTTPException(503, "Prepared vector batch unavailable") from None

        @app.api_route(PREFIX + "/vectors/{digest}", methods=["GET", "HEAD"])
        def prepared_vector(digest: str, request: Request):
            try:
                data = catalog.blob(digest)
                return Response(data if request.method == "GET" else b"", media_type="application/octet-stream",
                                headers={"Content-Length": str(len(data)), "ETag": '"' + digest + '"'})
            except KeyError:
                raise HTTPException(404, "Prepared vector not found") from None
            except (ValueError, OSError):
                raise HTTPException(503, "Prepared vector unavailable") from None

    app.add_middleware(GatewayBoundary, store=store, admission=admission, catalog=catalog,
                       recommendations=recommendations)
    return app
