"""Local parent-authorized controls and one opt-in listener in the current process."""
import asyncio
import hmac
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mobile_gateway import ENDPOINT, create_mobile_gateway, strict_json
from mobile_identity import ServerIdentity
from mobile_catalog import PreparedCatalog
from portable_pairing import PairingStore, secret_digest


def read_parent_capability(stream=None, env=None):
    env = os.environ if env is None else env
    if env.pop("RADIO_MOBILE_CONTROL_PIPE", None) != "1":
        return None
    # Only called by __main__. The parent writes one bounded line then closes.
    # A daemon reader bounds startup even if a broken parent forgets to close.
    import queue
    import threading
    result = queue.Queue(maxsize=1)
    def read():
        try:
            result.put((stream or sys.stdin.buffer).readline(1025))
        except Exception:
            result.put(b"")
    threading.Thread(target=read, daemon=True, name="mobile-parent-bootstrap").start()
    try:
        line = result.get(timeout=3)
        if len(line) > 1024 or not line.endswith(b"\n"):
            return None
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate parent field")
                value[key] = item
            return value
        value = json.loads(line, object_pairs_hook=unique)
        if set(value) != {"protocol_version", "capability"} or type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
            return None
        secret_digest(value["capability"])
        return value["capability"]
    except (ValueError, TypeError, KeyError, queue.Empty):
        return None


class GatewayListener:
    def __init__(self, app):
        self.app, self.server, self.task, self.socket = app, None, None, None

    async def start(self):
        import uvicorn
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Exclusive on Windows: another process cannot share/hijack this port.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", 18766))
            sock.listen(32)
            sock.setblocking(False)
            self.socket = sock
            self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=18766,
                lifespan="off", proxy_headers=False, access_log=False, log_config=None,
                server_header=False, timeout_keep_alive=2, limit_concurrency=16))
            # This auxiliary server must never take main's signal ownership.
            from contextlib import contextmanager
            @contextmanager
            def no_signals():
                yield
            self.server.capture_signals = no_signals
            self.server.install_signal_handlers = lambda: None
            self.task = asyncio.create_task(self.server.serve(sockets=[sock]), name="mobile-usb-gateway")
            self.task.add_done_callback(lambda _: self.app.state.admission.disable_all())
            async with asyncio.timeout(3):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("Mobile listener failed to start")
                    await asyncio.sleep(.01)
        except BaseException:
            await self.stop()
            sock.close()
            raise

    async def stop(self):
        if self.server:
            self.server.should_exit = True
        try:
            if self.task:
                try:
                    await asyncio.wait_for(asyncio.shield(self.task), 5)
                except TimeoutError:
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
                finally:
                    self.task = None
        finally:
            if self.socket:
                self.socket.close()
                self.socket = None


class MobileHosting:
    def __init__(self, store_factory, identity_path, *, capability=None, protector=None, listener_factory=GatewayListener,
                 catalog_factory=None, catalog_root=None, ingest_busy=lambda: False, ingest_epoch=lambda: 0,
                 monotonic=time.monotonic):
        self.store_factory, self.identity_path = store_factory, Path(identity_path)
        self.capability, self.protector, self.listener_factory = capability, protector, listener_factory
        self.identity, self.pairing, self.gateway, self.listener = None, None, None, None
        self._lock = asyncio.Lock()
        self._tickets_lock = threading.Lock()
        self.monotonic = monotonic
        self.catalog = PreparedCatalog(catalog_factory, root=catalog_root, ingest_busy=ingest_busy,
                                       ingest_epoch=ingest_epoch) if catalog_factory else None

    def status(self):
        return {"enabled": bool(self.gateway and self.gateway.state.admission.usb_enabled and self.listener),
                "endpoint": ENDPOINT, "server_id": self.identity.server_id if self.identity else None}

    def remote_status(self, *, publish=True):
        admission = self.gateway.state.admission if self.gateway else None
        active = bool(admission and self.listener and admission.remote_is_live(self.monotonic()))
        if not admission or not admission.remote_endpoint:
            return {"enabled": False, "endpoint": None, "connection_generation": None,
                    "lease_expires_in_seconds": 0, "descriptor": None}
        remaining = max(0, admission.remote_deadline - self.monotonic())
        signer = self.identity.publish_descriptor if publish else self.identity.descriptor
        descriptor = signer(admission.remote_endpoint, admission.remote_generation) if active else None
        return {"enabled": active, "endpoint": admission.remote_endpoint,
                "connection_generation": admission.remote_generation,
                "lease_expires_in_seconds": min(15, int(remaining)), "descriptor": descriptor}

    @staticmethod
    def validate_remote_endpoint(endpoint):
        if not isinstance(endpoint, str) or len(endpoint) > 256 or endpoint != endpoint.strip():
            raise ValueError("Invalid Quick Tunnel endpoint")
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
                    or parsed.port is not None or parsed.path or parsed.query or parsed.fragment
                    or parsed.netloc != host or host != host.lower()
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com", host)):
                raise ValueError("Invalid Quick Tunnel endpoint")
        except ValueError as exc:
            raise ValueError("Invalid Quick Tunnel endpoint") from exc
        return "https://" + host

    async def _ensure_gateway_listener(self):
        if not self.capability:
            raise RuntimeError("Parent authorization unavailable")
        if self.listener and getattr(getattr(self.listener, "task", None), "done", lambda: False)():
            if self.gateway:
                self.gateway.state.admission.disable_all()
            await self.listener.stop()
            self.listener = None
        if not self.identity:
            self.identity = await asyncio.to_thread(ServerIdentity, self.identity_path, protector=self.protector)
        if not self.gateway:
            store = await asyncio.to_thread(self.store_factory)
            self.pairing = await asyncio.to_thread(PairingStore, store, self.identity.server_id)
            await asyncio.to_thread(self._cancel_unused)
            self.gateway = create_mobile_gateway(store, self.identity, self.pairing, catalog=self.catalog,
                                                 monotonic=self.monotonic)
        if not self.listener:
            listener = self.listener_factory(self.gateway)
            try:
                await listener.start()
            except BaseException:
                await listener.stop()
                raise
            self.listener = listener

    async def enable(self):
        async with self._lock:
            if self.status()["enabled"]:
                return self.status()
            try:
                await self._ensure_gateway_listener()
                generation = await asyncio.to_thread(self.identity.next_generation)
                self.gateway.state.admission.enable(generation)
            except BaseException:
                if self.gateway:
                    self.gateway.state.admission.disable()
                raise
            return self.status()

    async def start_remote(self, endpoint):
        endpoint = self.validate_remote_endpoint(endpoint)
        async with self._lock:
            await self._ensure_gateway_listener()
            admission = self.gateway.state.admission
            if admission.remote_endpoint == endpoint and admission.remote_is_live(self.monotonic()):
                admission.renew_remote(admission.remote_generation, self.monotonic())
            else:
                generation = await asyncio.to_thread(self.identity.next_generation)
                admission.enable_remote(endpoint, generation, self.monotonic())
            return self.remote_status()

    async def renew_remote(self, generation):
        async with self._lock:
            admission = self.gateway.state.admission if self.gateway else None
            if (not admission or type(generation) is not int or generation != admission.remote_generation
                    or not admission.renew_remote(generation, self.monotonic())):
                raise ValueError("Remote generation is stale or its owner lease expired")
            return self.remote_status(publish=False)

    async def stop_remote(self, generation):
        async with self._lock:
            admission = self.gateway.state.admission if self.gateway else None
            if not admission or type(generation) is not int or generation != admission.remote_generation:
                raise ValueError("Remote generation is stale")
            admission.disable_remote()
            if not admission.usb_enabled and self.listener:
                await self.listener.stop()
                self.listener = None
            return self.remote_status()

    async def disable(self):
        async with self._lock:
            if self.gateway:
                self.gateway.state.admission.disable()
                self.gateway.state.admission.disable_remote()
            if self.listener:
                await self.listener.stop()
                self.listener = None
            if self.pairing:
                await asyncio.to_thread(self._cancel_unused)
            return self.status()

    def _cancel_unused(self):
        with self._tickets_lock:
            self.pairing.cancel_unused()

    def bundle(self, name):
        with self._tickets_lock:
            if not self.status()["enabled"]:
                raise ValueError("Enable USB pairing first")
            self.pairing.cancel_unused()
            ticket = self.pairing.create_ticket(name)
            return {"protocol_version": 1, "server_id": self.identity.server_id,
                    "public_key_pin": self.identity.public_key_pin, "endpoint": ENDPOINT,
                    "device_name": name, **ticket}


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str = Field(min_length=1, max_length=80)


class RemoteStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=256)


class RemoteGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_generation: int = Field(strict=True, ge=1)


def create_mobile_hosting_router(hosting):
    router = APIRouter(prefix="/api/mobile-hosting", tags=["local-mobile-admin"])

    def authorize(request):
        try:
            raw_headers = request.scope.get("headers", ())
            if len(raw_headers) > 64 or sum(len(k) + len(v) for k, v in raw_headers) > 8192:
                raise ValueError()
            seen = set()
            for key, _ in raw_headers:
                key = key.lower()
                if key in seen:
                    raise ValueError()
                seen.add(key)
            # These are desktop-admin routes: ApiSecurity validates Origin and
            # session cookies before dispatch. Electron supplies both legitimately.
            # The public mobile gateway retains its separate native-only policy.
            if any(key in seen for key in (b"transfer-encoding", b"content-encoding")):
                raise ValueError()
            local = ipaddress.ip_address(request.client.host).is_loopback
            headers = request.headers.getlist("x-radio-mobile-owner")
            valid = len(headers) == 1 and hosting.capability and hmac.compare_digest(headers[0], hosting.capability)
            if not local or not valid or request.scope.get("portable_identity"):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(403, "Local parent authorization required") from None

    @router.get("/status")
    def status(request: Request):
        authorize(request)
        return hosting.status()

    @router.post("/enable")
    async def enable(request: Request):
        authorize(request)
        try:
            return await hosting.enable()
        except Exception:
            raise HTTPException(503, "USB gateway unavailable; check protected identity or port") from None

    @router.post("/disable")
    async def disable(request: Request):
        authorize(request)
        return await hosting.disable()

    async def control_json(request, model):
        try:
            length = request.headers.get("content-length", "0")
            if not re.fullmatch(r"0|[1-9][0-9]{0,3}", length) or int(length) > 2048:
                raise ValueError()
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                raise ValueError()
            raw = await request.body()
            if len(raw) > 2048 or len(raw) != int(length):
                raise ValueError()
            return model.model_validate(strict_json(raw))
        except Exception:
            raise HTTPException(400, "Invalid mobile hosting control request") from None

    @router.get("/remote/status")
    def remote_status(request: Request):
        authorize(request)
        return hosting.remote_status()

    @router.post("/remote/start")
    async def remote_start(request: Request):
        authorize(request)
        body = await control_json(request, RemoteStartRequest)
        try:
            return await hosting.start_remote(body.endpoint)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(503, "Remote gateway unavailable; check protected identity or listener") from None

    @router.post("/remote/renew")
    async def remote_renew(request: Request):
        authorize(request)
        body = await control_json(request, RemoteGenerationRequest)
        try:
            return await hosting.renew_remote(body.connection_generation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/remote/stop")
    async def remote_stop(request: Request):
        authorize(request)
        body = await control_json(request, RemoteGenerationRequest)
        try:
            return await hosting.stop_remote(body.connection_generation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/pairing-tickets")
    def ticket(body: TicketRequest, request: Request):
        authorize(request)
        try:
            return hosting.bundle(body.device_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/devices")
    def devices(request: Request):
        authorize(request)
        return {"devices": PairingStore.devices()}

    @router.get("/catalog/status")
    def catalog_status(request: Request):
        authorize(request)
        if hosting.catalog is None:
            raise HTTPException(503, "Catalog preparation unavailable")
        return hosting.catalog.status()

    @router.post("/catalog/prepare", status_code=202)
    def prepare_catalog(request: Request):
        authorize(request)
        if hosting.catalog is None:
            raise HTTPException(503, "Catalog preparation unavailable")
        try:
            return hosting.catalog.prepare()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/catalog/cancel")
    def cancel_catalog(request: Request):
        authorize(request)
        if hosting.catalog is None:
            raise HTTPException(503, "Catalog preparation unavailable")
        return hosting.catalog.cancel()

    @router.delete("/devices/{device_id}")
    def revoke(device_id: str, request: Request):
        authorize(request)
        hosting.store_factory().revoke_device(device_id)
        return {"revoked": True}

    @router.post("/devices/{device_id}/profile-sync")
    async def grant_profile_sync(device_id: str, request: Request):
        authorize(request)
        try:
            # This explicit local-admin action also starts the listener so an
            # already-paired phone can sync after a desktop restart.
            await hosting.enable()
            return hosting.store_factory().grant_profile_sync(device_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        except Exception:
            raise HTTPException(503, "USB gateway unavailable; check protected identity or port") from None

    return router
