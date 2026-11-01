"""Local parent-authorized controls and one opt-in listener in the current process."""
import asyncio
import hmac
import ipaddress
import json
import os
import socket
from pathlib import Path
import sys
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mobile_gateway import ENDPOINT, create_mobile_gateway
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
            self.task.add_done_callback(lambda _: self.app.state.admission.disable())
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
                 catalog_factory=None, catalog_root=None, ingest_busy=lambda: False, ingest_epoch=lambda: 0):
        self.store_factory, self.identity_path = store_factory, Path(identity_path)
        self.capability, self.protector, self.listener_factory = capability, protector, listener_factory
        self.identity, self.pairing, self.gateway, self.listener = None, None, None, None
        self._lock = asyncio.Lock()
        self._tickets_lock = threading.Lock()
        self.catalog = PreparedCatalog(catalog_factory, root=catalog_root, ingest_busy=ingest_busy,
                                       ingest_epoch=ingest_epoch) if catalog_factory else None

    def status(self):
        return {"enabled": bool(self.gateway and self.gateway.state.admission.enabled and self.listener),
                "endpoint": ENDPOINT, "server_id": self.identity.server_id if self.identity else None}

    async def enable(self):
        async with self._lock:
            if self.status()["enabled"]:
                return self.status()
            if self.listener:
                await self.listener.stop()
                self.listener = None
            if not self.capability:
                raise RuntimeError("Parent authorization unavailable")
            # Creation is lazy; protected storage failure leaves admission closed.
            if not self.identity:
                self.identity = await asyncio.to_thread(ServerIdentity, self.identity_path, protector=self.protector)
            store = await asyncio.to_thread(self.store_factory)
            self.pairing = await asyncio.to_thread(PairingStore, store, self.identity.server_id)
            await asyncio.to_thread(self._cancel_unused)
            self.gateway = create_mobile_gateway(store, self.identity, self.pairing, catalog=self.catalog)
            listener = self.listener_factory(self.gateway)
            try:
                generation = await asyncio.to_thread(self.identity.next_generation)
                await listener.start()
                self.gateway.state.admission.enable(generation)
                self.listener = listener
            except BaseException:
                self.gateway.state.admission.disable()
                await listener.stop()
                raise
            return self.status()

    async def disable(self):
        async with self._lock:
            if self.gateway:
                self.gateway.state.admission.disable()
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


def create_mobile_hosting_router(hosting):
    router = APIRouter(prefix="/api/mobile-hosting", tags=["local-mobile-admin"])

    def authorize(request):
        try:
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

    return router
