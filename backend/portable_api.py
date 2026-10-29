"""Small native-client protocol behind existing HTTP security and scoped tokens."""
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from portable_sync import SyncConflict

PREFIX = "/api/portable/v1"


def native_authorizer(store_factory):
    def authorize(scope):
        path = scope.get("path", "")
        # Token administration always uses the original cookie/loopback boundary.
        if not path.startswith(PREFIX + "/") or path.startswith(PREFIX + "/devices"):
            return None
        values = [value.decode("latin1") for key, value in scope.get("headers", ()) if key.lower() == b"authorization"]
        if len(values) != 1 or not values[0].startswith("Bearer "):
            return None
        return store_factory().authorize(values[0][7:])
    return authorize


class DeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=4)


class EventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    events: list[dict] = Field(min_length=1, max_length=500)
    device_id: str | None = None


def create_portable_router(store_factory, catalog_service):
    router = APIRouter(prefix=PREFIX, tags=["portable-v1"])

    def require(request, scope):
        identity = request.scope.get("portable_identity")
        if identity and scope not in identity["scopes"]:
            raise HTTPException(403, "Device token lacks required scope")
        return identity

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SyncConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "Portable resource not found") from None

    @router.post("/devices")
    def issue(body: DeviceRequest):
        return call(store_factory().issue_device, body.name, body.scopes)

    @router.delete("/devices/{device_id}")
    def revoke(device_id: str):
        call(store_factory().revoke_device, device_id)
        return {"revoked": True}

    @router.get("/capabilities")
    def capabilities():
        return {"protocol_version": 1, "catalog_schema": 1, "matching_schema": 1,
                "profile_schema": 1, "media_plan_schema": 1, "automatic_sync": False,
                "local_music_required": True, "offline_recommendations": True,
                "native_speech_generation": False, "max_event_batch": 500,
                "event_order": ["logical_clock", "device_id", "event_id"]}

    @router.get("/catalog")
    def catalog(request: Request, revision: int | None = Query(default=None, ge=1),
                base_revision: int | None = Query(default=None, ge=1)):
        require(request, "catalog:read")
        return call(catalog_service().manifest, revision, base_revision)

    @router.api_route("/vectors/{digest}", methods=["GET", "HEAD"])
    def vector(digest: str, request: Request):
        require(request, "catalog:read")
        path = call(catalog_service().blob, digest)
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"ETag": '"' + digest + '"', "Cache-Control": "private, max-age=31536000, immutable"})

    @router.get("/events")
    def events(request: Request, cursor: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
        require(request, "profile:read")
        store = store_factory()
        call(store.capture_desktop)
        return call(store.events, cursor, limit)

    @router.post("/events")
    def import_events(body: EventsRequest, request: Request):
        identity = require(request, "profile:write")
        if body.protocol_version != 1:
            raise HTTPException(400, "Unsupported synchronization protocol")
        device = identity["device_id"] if identity else body.device_id
        if not device:
            raise HTTPException(400, "Device identity required")
        store = store_factory()
        # Capture unsynchronized desktop choices before importing remote choices.
        # Active playback/session state is outside the shared profile.
        call(store.capture_desktop)
        return call(store.import_events, device, body.events)

    @router.get("/profile")
    def profile(request: Request):
        require(request, "profile:read")
        store = store_factory()
        call(store.capture_desktop)
        return store.profile()

    return router
