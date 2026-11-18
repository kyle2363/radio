"""/api/feels routes for the desktop Feels panel (checkpoints 9D-9E)."""
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel


class FeelBody(BaseModel):
    name: str | None = None
    color: str | None = None
    archived: bool | None = None


class RateBody(BaseModel):
    song_id: int
    value: float | int | str


class CompareBody(BaseModel):
    a: int
    b: int
    outcome: str


class StationBody(BaseModel):
    feel_id: str | None = None


class SettingsBody(BaseModel):
    suggestions: str


def create_feels_router(service_factory, on_station_change=lambda: None):
    router = APIRouter()

    async def call(fn, *args, **kwargs):
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0] if exc.args else exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    def svc():
        return service_factory()

    @router.get("/api/feels")
    async def list_feels(include_archived: bool = False):
        from feel_service import starter_feels
        feels = await call(svc().list_feels, include_archived)
        defined = {f["id"] for f in await call(svc().list_feels, True)}
        return {"feels": feels, "starters": [s for s in starter_feels() if s["id"] not in defined]}

    @router.post("/api/feels")
    async def create_feel(body: FeelBody):
        return {"id": await call(svc().define, None, name=body.name, color=body.color)}

    @router.post("/api/feels/starters/{key}")
    async def adopt(key: str):
        return {"id": await call(svc().adopt_starter, key)}

    @router.get("/api/feels/station")
    async def get_station():
        return {"feel_id": await call(svc().station)}

    @router.put("/api/feels/station")
    async def put_station(body: StationBody):
        await call(svc().set_station, body.feel_id)
        on_station_change()
        return {"feel_id": body.feel_id}

    @router.get("/api/feels/settings")
    async def get_settings():
        return {"suggestions": await call(svc().suggestion_mode)}

    @router.put("/api/feels/settings")
    async def put_settings(body: SettingsBody):
        await call(svc().set_suggestion_mode, body.suggestions)
        return {"suggestions": body.suggestions}

    @router.get("/api/feels/session-suggestions")
    async def session_suggestions():
        if svc().suggestion_mode() != "after_session":
            return {"songs": []}
        return {"songs": await call(svc().session_suggestions)}

    @router.get("/api/feels/{feel_id}")
    async def ranking(feel_id: str):
        return await call(svc().ranking, feel_id)

    @router.patch("/api/feels/{feel_id}")
    async def edit(feel_id: str, body: FeelBody):
        return {"id": await call(svc().define, feel_id, name=body.name, color=body.color, archived=body.archived)}

    @router.post("/api/feels/{feel_id}/rate")
    async def rate(feel_id: str, body: RateBody):
        await call(svc().rate, feel_id, body.song_id, body.value)
        return {"ok": True}

    @router.post("/api/feels/{feel_id}/compare")
    async def compare(feel_id: str, body: CompareBody):
        await call(svc().compare, feel_id, body.a, body.b, body.outcome)
        return {"ok": True}

    @router.get("/api/feels/{feel_id}/sprint")
    async def sprint(feel_id: str, n: int = 20):
        return {"songs": await call(svc().sprint, feel_id, max(1, min(n, 50)))}

    @router.get("/api/feels/{feel_id}/pairs")
    async def pairs(feel_id: str, n: int = 10):
        return {"pairs": await call(svc().pairs, feel_id, max(1, min(n, 20)))}

    @router.get("/api/feels/{feel_id}/review")
    async def review(feel_id: str):
        return {"songs": await call(svc().review, feel_id)}

    @router.get("/api/songs/{song_id}/feels")
    async def song_feels(song_id: int):
        service = svc()
        mode = service.suggestion_mode()
        return {"history": await call(service.history, song_id),
                "suggestions": await call(service.suggestions, song_id) if mode == "song_page" else []}

    return router
