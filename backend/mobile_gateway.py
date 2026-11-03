"""Deliberately separate, USB-only ASGI surface; never mounts desktop routes."""
import asyncio
from collections import OrderedDict
import ipaddress
import json
import math
import re
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response

from portable_sync import SyncConflict
from portable.catalog import canonical_json
from mobile_catalog import MAX_REVISION, MOBILE_BATCH_COUNT, MOBILE_BATCH_BYTES

ENDPOINT = "http://127.0.0.1:18766"
PREFIX = "/api/portable/v1"
ROUTES = {("GET", PREFIX + "/identity"), ("POST", PREFIX + "/pairing/exchange"),
          ("GET", PREFIX + "/capabilities")}


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
    def __init__(self):
        self.enabled, self.generation = False, 0

    def enable(self, generation):
        self.generation, self.enabled = generation, True

    def disable(self):
        self.enabled = False


class GatewayBoundary:
    def __init__(self, app, *, store, admission, catalog=None):
        self.app, self.store, self.admission = app, store, admission
        self.catalog = catalog
        self._lock, self._buckets = threading.Lock(), OrderedDict()
        self._global = (0, 0)

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

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)

        async def reject(status, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=status,
                               headers={"Cache-Control": "no-store", **(headers or {})})(scope, receive, send)

        if not self.admission.enabled:
            return await reject(503, "Mobile gateway disabled")
        path = scope.get("path", "")
        is_catalog = path == PREFIX + "/catalog" and scope["method"] == "GET"
        is_batch = path == PREFIX + "/vector-batch" and scope["method"] == "GET"
        is_vector = bool(re.fullmatch(re.escape(PREFIX) + r"/vectors/[0-9a-f]{64}", path)) and scope["method"] in ("GET", "HEAD")
        # The factory supplies this flag explicitly; middleware's inner app is
        # an exception wrapper rather than the FastAPI instance.
        catalog_enabled = self.catalog is not None
        if (((scope["method"], path) not in ROUTES and not (catalog_enabled and (is_catalog or is_vector or is_batch)))
                or (scope.get("query_string") and not (is_catalog or is_batch))
                or scope.get("raw_path", path.encode()) != path.encode("ascii", errors="replace")):
            return await reject(404, "Mobile route not found")
        try:
            client = scope["client"][0]
            if not ipaddress.ip_address(client).is_loopback:
                return await reject(403, "USB loopback only")
            headers = scope.get("headers", ())
            if len(headers) > 64 or sum(len(k) + len(v) for k, v in headers) > 8192:
                return await reject(431, "Headers too large")
            values = {}
            for key, value in headers:
                key = key.lower()
                if key in values:
                    return await reject(400, "Duplicate header")
                values[key] = value.decode("latin1")
            if values.get(b"host") != "127.0.0.1:18766":
                return await reject(400, "Exact gateway Host required")
            if any(k.startswith(b"x-forwarded-") or k in (b"forwarded", b"x-real-ip", b"transfer-encoding") for k in values):
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
            if (path.endswith("/capabilities") or is_catalog or is_vector or is_batch) and not scope.get("portable_identity"):
                return await reject(401, "Device bearer required")
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
            length = values.get(b"content-length", "0")
            if not re.fullmatch(r"0|[1-9][0-9]{0,8}", length):
                return await reject(400, "Invalid content length")
            if int(length) > 8192:
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
                    if len(body) > 8192:
                        return await reject(413, "Mobile body too large")
                    if not message.get("more_body"):
                        break
            if (b"content-length" in values and int(length) != len(body)) or (scope["method"] in ("GET", "HEAD") and body):
                return await reject(400, "Unexpected body framing")
        except (ValueError, KeyError, TypeError):
            return await reject(400, "Invalid gateway request")
        except TimeoutError:
            return await reject(408, "Mobile request timed out")

        async def buffered():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)
        await self.app(scope, buffered, private_send)


def create_mobile_gateway(store, identity, pairing, *, catalog=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    admission = app.state.admission = Admission()

    @app.get(PREFIX + "/identity")
    def descriptor():
        return identity.descriptor(ENDPOINT, admission.generation, 1)

    @app.post(PREFIX + "/pairing/exchange")
    async def exchange(request: Request):
        try:
            # Reject duplicate object keys instead of silently retaining one.
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON field")
                    result[key] = value
                return result
            body = json.loads(await request.body(), object_pairs_hook=unique)
            return await asyncio.to_thread(pairing.exchange, body)
        except SyncConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid, expired or revoked pairing exchange") from None

    @app.get(PREFIX + "/capabilities")
    def capabilities(request: Request):
        return {"protocol_version": 1, "server_id": identity.server_id,
                "authenticated_device_id": request.scope["portable_identity"]["device_id"],
                "catalog_id": store.catalog_id, "pairing": True,
                "catalog_import": catalog is not None, "catalog_schema": 1,
                "catalog_revision": catalog.status()["revision"] if catalog else None,
                "profile_sync": False, "remote_hosting": False,
                "offline_recommendations": False, "native_speech_generation": False,
                "local_music_required": True}

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

    app.add_middleware(GatewayBoundary, store=store, admission=admission, catalog=catalog)
    return app
