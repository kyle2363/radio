"""Connected recommendations for paired phones (checkpoint 4).

Everything here is in memory and read-only with respect to the desktop: it
holds one availability snapshot and at most two contexts per device, ranks with
the shared pure ``engine.recommendation_service``, and answers over the mobile
gateway. It never touches the desktop queue, playback sessions, media service,
the playback coordinator or the WebSocket broadcast helper, and it never writes
``play_events`` or ``queue_history``. The phone's recent-session list shapes
only the request it travels with.
"""
import hashlib
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Annotated, Optional
from uuid import uuid4

import config
from db import database
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError

from engine import mood as mood_module
from engine import recommendation_service as service
from engine import taste

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_SONG_IDS = 20000
MAX_RECENT = 20
MAX_EXCLUDED = 200
MAX_CONTEXTS_PER_DEVICE = 2
CONTEXT_IDLE_SECONDS = 1800.0
DEADLINE_SECONDS = 60.0
MAX_TOTAL_CONTEXTS = 128
MAX_SNAPSHOTS = 64
MOODS = ("chill", "neutral", "energetic", "focus", "melancholy", "discovery")
SONG_ID = r"[1-9][0-9]{0,17}"
CONTEXT_ID = r"[A-Za-z0-9_-]{16,64}"

SongId = Annotated[StrictStr, Field(pattern=r"^" + SONG_ID + r"$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Number = StrictFloat | StrictInt


class Rejected(Exception):
    """A request the service refuses; the gateway turns it into an HTTP error."""

    def __init__(self, status, detail):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def conflict(reason):
    return Rejected(409, {"reason": reason})


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    catalog_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    catalog_revision: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    song_ids: Annotated[list[SongId], Field(min_length=1, max_length=MAX_SONG_IDS)]
    digest: Digest


class RecentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song_id: SongId
    outcome: Annotated[StrictStr, Field(pattern=r"^(completed|skipped)$")]


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    mood: Annotated[StrictStr, Field(pattern=r"^(" + "|".join(MOODS) + r")$")]
    exploration: Number
    vibe_lock_song_id: Optional[SongId] = None
    recent: Annotated[list[RecentEntry], Field(max_length=MAX_RECENT)]


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    request_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    epoch: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    availability_digest: Digest
    catalog_revision: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    anchor_song_id: SongId
    exclude_song_ids: Annotated[list[SongId], Field(max_length=MAX_EXCLUDED)]


def availability_digest(song_ids):
    """SHA-256 of the distinct IDs sorted by UTF-8 bytes and joined with newlines."""
    joined = "\n".join(sorted(set(song_ids), key=lambda value: value.encode("utf-8")))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _parse(model, body):
    try:
        value = model.model_validate(body)
    except ValidationError:
        raise Rejected(422, "Invalid request") from None
    if value.protocol_version != PROTOCOL_VERSION:
        raise Rejected(400, "Unsupported protocol")
    return value


def _existing_ids(ids):
    """Which of ``ids`` (ints) are rows of the desktop library."""
    import json
    if not ids:
        return set()
    with database.get_connection_cm() as conn:
        rows = conn.execute("SELECT id FROM songs WHERE id IN (SELECT value FROM json_each(?))",
                            (json.dumps(sorted(ids)),)).fetchall()
    return {row[0] for row in rows}


@dataclass
class Snapshot:
    catalog_id: str
    catalog_revision: int
    digest: str
    ids: frozenset
    used: int = 0


@dataclass
class Context:
    context_id: str
    device_id: str
    epoch: int
    mood: str
    exploration: float
    vibe_lock_song_id: Optional[int]
    recent: list
    touched: float
    order: int


@dataclass
class _Ticket:
    seq: int
    state: str = "waiting"      # waiting | go | superseded
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class _DeviceWork:
    latest: int = 0
    running: Optional[_Ticket] = None
    pending: Optional[_Ticket] = None


class MobileRecommendations:
    def __init__(self, store, *, clock=time.monotonic, deadline_seconds=DEADLINE_SECONDS,
                 idle_seconds=CONTEXT_IDLE_SECONDS, gate=None, recommend_fn=None, memory=None):
        self.store, self.clock = store, clock
        self.deadline_seconds, self.idle_seconds = float(deadline_seconds), float(idle_seconds)
        self.gate = gate if gate is not None else service.DEFAULT_LLM_GATE
        self.recommend_fn = recommend_fn or (lambda inputs: service.recommend(inputs))
        self.memory = memory or taste.MemorySystem()
        self._lock = threading.RLock()
        self._snapshots = OrderedDict()           # device_id -> Snapshot (LRU)
        self._contexts = {}                       # context_id -> Context
        self._work = {}                           # device_id -> _DeviceWork
        self._counter = 0

    # -- availability -----------------------------------------------------

    def put_availability(self, device_id, body):
        request = _parse(AvailabilityRequest, body)
        if request.catalog_id != self.store.catalog_id:
            raise conflict("catalog_mismatch")
        if len(set(request.song_ids)) != len(request.song_ids):
            raise Rejected(400, "Duplicate song IDs")
        if availability_digest(request.song_ids) != request.digest:
            raise Rejected(400, "Availability digest does not match song_ids")
        with self._lock:
            current = self._snapshots.get(device_id)
            if current and current.digest == request.digest and current.catalog_id == request.catalog_id:
                # Same set: nothing to re-validate; only follow a catalog revision bump.
                current.catalog_revision = request.catalog_revision
                self._snapshots.move_to_end(device_id)
                return self._availability_reply(current)
        ids = frozenset(int(value) for value in request.song_ids)
        if _existing_ids(ids) != ids:
            raise Rejected(400, "Unknown song IDs")
        snapshot = Snapshot(request.catalog_id, request.catalog_revision, request.digest, ids)
        with self._lock:
            self._snapshots[device_id] = snapshot
            self._snapshots.move_to_end(device_id)
            while len(self._snapshots) > MAX_SNAPSHOTS:
                self._snapshots.popitem(last=False)
        return self._availability_reply(snapshot)

    @staticmethod
    def _availability_reply(snapshot):
        return {"digest": snapshot.digest, "count": len(snapshot.ids),
                "catalog_id": snapshot.catalog_id, "catalog_revision": snapshot.catalog_revision}

    # -- contexts ---------------------------------------------------------

    def _sweep(self):
        now = self.clock()
        for key in [k for k, c in self._contexts.items() if now - c.touched >= self.idle_seconds]:
            del self._contexts[key]

    def _fields(self, body):
        request = _parse(ContextRequest, body)
        if not 0 <= float(request.exploration) <= 1:
            raise Rejected(422, "Invalid request")
        recent = [(int(item.song_id), item.outcome) for item in request.recent]
        vibe = int(request.vibe_lock_song_id) if request.vibe_lock_song_id else None
        known = _existing_ids({sid for sid, _ in recent} | ({vibe} if vibe else set()))
        if any(sid not in known for sid, _ in recent) or (vibe and vibe not in known):
            raise Rejected(400, "Unknown song IDs")
        return request.mood, float(request.exploration), vibe, recent

    def create_context(self, device_id, body):
        mood, exploration, vibe, recent = self._fields(body)
        with self._lock:
            self._sweep()
            mine = sorted((c for c in self._contexts.values() if c.device_id == device_id), key=lambda c: c.order)
            while len(mine) >= MAX_CONTEXTS_PER_DEVICE:
                del self._contexts[mine.pop(0).context_id]
            while len(self._contexts) >= MAX_TOTAL_CONTEXTS:
                oldest = min(self._contexts.values(), key=lambda c: c.order)
                del self._contexts[oldest.context_id]
            self._counter += 1
            context = Context(secrets.token_urlsafe(24), device_id, 1, mood, exploration, vibe, recent,
                              self.clock(), self._counter)
            self._contexts[context.context_id] = context
            return self._context_reply(context)

    def update_context(self, device_id, context_id, body):
        with self._lock:
            self._owned(device_id, context_id)
        mood, exploration, vibe, recent = self._fields(body)
        with self._lock:
            context = self._owned(device_id, context_id)
            context.mood, context.exploration, context.vibe_lock_song_id, context.recent = mood, exploration, vibe, recent
            context.epoch += 1
            self._touch(context)
            return self._context_reply(context)

    def _owned(self, device_id, context_id):
        self._sweep()
        context = self._contexts.get(context_id)
        if context is None or context.device_id != device_id:
            raise Rejected(404, "context_not_found")
        return context

    def _touch(self, context):
        self._counter += 1
        context.order, context.touched = self._counter, self.clock()

    def _context_reply(self, context):
        return {"context_id": context.context_id, "epoch": context.epoch, "expires_in": int(self.idle_seconds)}

    def forget_device(self, device_id):
        """Drop all in-memory state for a device (revoked or scope removed)."""
        with self._lock:
            self._snapshots.pop(device_id, None)
            for key in [k for k, c in self._contexts.items() if c.device_id == device_id]:
                del self._contexts[key]

    # -- recommendations --------------------------------------------------

    def recommend(self, device_id, context_id, body):
        arrival = self.clock()
        with self._lock:
            self._owned(device_id, context_id)          # unknown / foreign / expired -> 404 first
        request = _parse(RecommendationRequest, body)
        with self._lock:
            context = self._owned(device_id, context_id)
            self._touch(context)
            self._check_fences(device_id, context, request)
            anchor = int(request.anchor_song_id)
            snapshot = self._snapshots[device_id]
        if anchor not in _existing_ids({anchor}):
            raise Rejected(400, "Unknown anchor song")
        ticket = self._admit(device_id)
        try:
            self._await_turn(device_id, ticket)
            result, vibe_locked = self._compute(context, snapshot, request, anchor, arrival + self.deadline_seconds)
        except BaseException:
            self._finish(device_id, ticket)
            raise
        with self._lock:
            superseded = self._work[device_id].latest != ticket.seq
        self._finish(device_id, ticket)
        if superseded:
            raise conflict("superseded")
        with self._lock:                                  # fence again: nothing changed while ranking
            context = self._owned(device_id, context_id)
            self._check_fences(device_id, context, request)
        return {
            "request_id": request.request_id, "result_id": uuid4().hex, "context_id": context_id,
            "epoch": request.epoch, "availability_digest": request.availability_digest,
            "catalog_revision": request.catalog_revision, "anchor_song_id": request.anchor_song_id,
            "status": result.status, "ranked_by": result.ranked_by, "vibe_locked": vibe_locked,
            "picks": [{"song_id": str(p["song_id"]), "slot": p["slot"], "reason": p["reason"]}
                      for p in result.picks],
        }

    def _check_fences(self, device_id, context, request):
        snapshot = self._snapshots.get(device_id)
        if snapshot is None:
            raise conflict("no_availability")
        if request.epoch != context.epoch:
            raise conflict("epoch_stale")
        if request.availability_digest != snapshot.digest or request.catalog_revision != snapshot.catalog_revision:
            raise conflict("availability_stale")

    # Admission: one running and one pending request per device.

    def _admit(self, device_id):
        with self._lock:
            work = self._work.setdefault(device_id, _DeviceWork())
            work.latest += 1
            ticket = _Ticket(work.latest)
            if work.running is None:
                work.running, ticket.state = ticket, "go"
                ticket.event.set()
            else:
                if work.pending is not None:
                    work.pending.state = "superseded"
                    work.pending.event.set()
                work.pending = ticket
            return ticket

    def _await_turn(self, device_id, ticket):
        if not ticket.event.wait(self.deadline_seconds * 2 + 5):
            with self._lock:
                work = self._work.get(device_id)
                if work and work.pending is ticket:
                    work.pending = None
            raise Rejected(503, "Recommendation service busy")
        if ticket.state == "superseded":
            raise conflict("superseded")

    def _finish(self, device_id, ticket):
        with self._lock:
            work = self._work.get(device_id)
            if work is None:
                return
            if work.running is ticket:
                work.running = None
                if work.pending is not None:
                    work.running, work.pending = work.pending, None
                    work.running.state = "go"
                    work.running.event.set()
            elif work.pending is ticket:
                work.pending = None
            if work.running is None and work.pending is None:
                del self._work[device_id]

    # Ranking

    def _compute(self, context, snapshot, request, anchor, deadline_at):
        with self._lock:
            mood_name, exploration = context.mood, context.exploration
            vibe_song, recent = context.vibe_lock_song_id, list(context.recent)
        completed = [sid for sid, outcome in reversed(recent) if outcome == "completed"]
        skip_streak = 0
        for _, outcome in reversed(recent):
            if outcome != "skipped":
                break
            skip_streak += 1
        exclude = {anchor, *(int(v) for v in request.exclude_song_ids), *(sid for sid, _ in recent)}
        vibe = self._vibe_lock(vibe_song)
        collections = [("text", "songs")]
        if config.MUQ_ENABLED:
            collections.append(("audio", config.MUQ_AUDIO_COLLECTION))
        vectors = {kind: self._taste_vector(name, completed, skip_streak) for kind, name in collections}
        try:
            text_repulsion = self.memory.get_repulsion_vector()
            audio_repulsion = self.memory.get_audio_repulsion_vector()
        except Exception:                                  # noqa: BLE001 - optional signal
            text_repulsion = audio_repulsion = None
        from engine import archetype as archetype_engine
        archetype_name, _ = archetype_engine.detect_archetype()
        mood_config = mood_module.MOOD_STATES[mood_name]
        reranking = (database.get_setting("llm_reranking_enabled") or "true").lower() != "false"
        inputs = service.RecommendationInputs(
            db=database, anchor_song_id=anchor,
            taste_vector=vectors["text"], audio_taste_vector=vectors.get("audio"),
            text_repulsion_vector=text_repulsion, audio_repulsion_vector=audio_repulsion,
            exclude_ids=sorted(exclude), mood_state=mood_name,
            mood_energy_range=mood_config["energy_range"], mood_valence_range=mood_config["valence_range"],
            skip_streak=skip_streak, exploration_level=exploration, vibe_lock=vibe,
            reranking_enabled=reranking, archetype=archetype_name,
            allowed_ids=snapshot.ids, filler_current_song_id=anchor, strict_exclusions=True,
            llm_budget_seconds=lambda: max(0.0, deadline_at - self.clock()),
            llm_gate=self.gate, gate_priority=service.LlmPriorityGate.MOBILE,
        )
        try:
            result = self.recommend_fn(inputs)
        except Exception:
            logger.exception("Connected recommendation failed")
            raise Rejected(503, "Recommendation unavailable") from None
        picks = [p for p in result.picks if p["song_id"] in snapshot.ids and p["song_id"] not in exclude]
        if picks != result.picks:
            result.picks = picks
            result.status = "ok" if picks else "insufficient"
        return result, bool(vibe)

    def _taste_vector(self, collection, completed_newest_first, skip_streak):
        """Shared long-term/daily profile plus this request's session list."""
        try:
            shared = self.memory.get_recent_signals(collection_name=collection)
            embeddings = self.memory._batch_get_chroma_embeddings(completed_newest_first, collection) if completed_newest_first else {}
            session = [{"song_id": sid, "embedding": embeddings[str(sid)], "timestamp": None, "signal_type": "completed"}
                       for sid in completed_newest_first if str(sid) in embeddings]
            if not shared and not session:
                return None
            vector, _ = self.memory.blend_signals(shared or session, session or None, skip_streak)
            return vector
        except Exception as exc:                           # noqa: BLE001 - cold start is acceptable
            logger.debug("Taste vector unavailable for %s: %s", collection, exc)
            return None

    def _vibe_lock(self, song_id):
        if not song_id:
            return None
        text = self.memory._get_chroma_embedding(song_id, "songs")
        audio = self.memory._get_chroma_embedding(song_id, config.MUQ_AUDIO_COLLECTION) if config.MUQ_ENABLED else None
        if text is None and audio is None:
            return None
        as_list = lambda v: None if v is None else [float(x) for x in v]
        return {"active": True, "embedding": as_list(text), "audio_embedding": as_list(audio), "songs_remaining": 10}


__all__ = ["MobileRecommendations", "Rejected", "availability_digest", "CONTEXT_ID"]
