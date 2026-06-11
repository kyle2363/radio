"""FastAPI entry point for the Radio backend."""

# Disable ChromaDB telemetry before any chromadb imports
import os
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"

# Patch posthog.capture BEFORE importing chromadb - this is the actual source of the error
try:
    import posthog
    posthog.capture = lambda *a, **k: None
    posthog.disabled = True
except ImportError:
    pass

import asyncio
import json
import logging
import sys
import threading
import tempfile
import time
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Form, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

import chromadb
import config

from db import database
from ingest import pipeline
from engine import player as player_engine
from engine import queue as queue_engine
from engine import mood as mood_engine
from engine import archetype as archetype_engine
from engine import energy as energy_engine
from engine.sleep_timer import SleepTimer

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("torio._extension.utils").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Song-end playback log (same file as player.py writes to)
_playback_logger = logging.getLogger("radio.playback")
if not _playback_logger.handlers:
    _handler = logging.FileHandler(config.LOG_PATH, mode="a", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%H:%M:%S"))
    _playback_logger.addHandler(_handler)
    _playback_logger.setLevel(logging.DEBUG)
    _playback_logger.propagate = False


@asynccontextmanager
async def lifespan(app):
    database.init_db()
    database.close_stale_sessions()
    database.cleanup_old_mood_repulsion(days=30)
    # Reset stale genre/artist/folder filters (session-only)
    if database.get_setting("genre_filter") == "jazz":
        database.set_setting("genre_filter", "")
    database.set_setting("folder_filter", "")
    print(f"Database initialized at {config.DB_PATH}")
    print(f"Music directory: {config.MUSIC_DIR}")
    print(f"Acestep JSON directory: {config.ACESTEP_JSON_DIR}")

    # ChromaDB health check (async, non-blocking)
    def _run_chroma_health():
        try:
            _chromadb_health_check()
        except Exception as e:
            logger.debug("Startup ChromaDB health check failed: %s", e)
    threading.Thread(target=_run_chroma_health, daemon=True).start()

    # Clear stale DJ effect cache files on startup
    from engine.dj_effects import cleanup_cache as cleanup_dj_cache
    cleanup_dj_cache(max_age_hours=24)

    # Clear stale TTS cache files on startup
    tts_cache_dir = config.TTS_CACHE_DIR
    if os.path.isdir(tts_cache_dir):
        removed = 0
        for fname in os.listdir(tts_cache_dir):
            fpath = os.path.join(tts_cache_dir, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    removed += 1
                except Exception:
                    pass
        if removed > 0:
            logger.info("Cleared %d stale TTS cache files", removed)

    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    _talk_ended_lock = threading.Lock()

    def _on_song_ended(song_id):
        _playback_logger.debug("=== main._on_song_ended(song=%s) ENTER — running in background thread", song_id)

        # Guard: prevent concurrent talk-ended callbacks.  VLC can fire two
        # MediaPlayerEndReached events for the same talk-only chain end (one
        # from _on_end_reached, one from _on_media_end_reached), which would
        # cause two threads racing to pop_next + play_next — the second one
        # may block the server by calling play_song() while the first holds
        # the player lock.
        if song_id is None and not _talk_ended_lock.acquire(blocking=False):
            _playback_logger.debug("  -> concurrent talk-ended callback detected, skipping duplicate")
            return

        try:
            # song_id can be None when a talk-only chain ends (no real song was playing)
            if song_id is not None:
                qm.on_song_ended(song_id)
                # DB "completed" event is already recorded by player.py _fire_song_ended
                qm.record_completion()
                song = database.get_song(song_id)
                if song:
                    energy_engine.get_trajectory().record_energy(song_id, song.get("energy", 0.5))
                broadcast("song_ended", {"song_id": song_id})
            else:
                _playback_logger.debug("  -> song_id is None (talk-only chain ended), skipping transition logic")

            # Auto-play the next song from the queue (with TTS if enabled)
            _playback_logger.debug("  -> calling qm.pop_next() (this may block up to 5s)")
            pick = qm.pop_next()
            _playback_logger.debug("  <- qm.pop_next() returned")
            if pick:
                if pick.get("type") == "talk_segment":
                    _playback_logger.debug("  -> pop_next returned talk_segment type=%s", pick.get("segment_type"))
                    _playback_logger.debug("  -> calling qm.play_next(talk_segment)...")
                    qm.play_next(pick)
                    _playback_logger.debug("  <- play_next done (talk segment)")
                else:
                    _playback_logger.debug("  -> pop_next returned song_id=%s slot=%s", pick.get("song_id"), pick.get("slot"))
                    session_id = _ensure_session()
                    database.record_play_event(
                        song_id=pick["song_id"],
                        session_id=session_id,
                        signal="playing",
                        queue_slot=pick.get("slot"),
                        mood_state=msm.get_state(),
                    )
                    _playback_logger.debug("  -> calling qm.play_next(song)...")
                    qm.play_next(pick)
                    qm.on_song_started(pick["song_id"], session_id)
                    broadcast("song_started", {"song_id": pick["song_id"], "session_id": session_id, "elapsed_ms": 0})
                    _playback_logger.debug("  <- play_next done")
            else:
                _playback_logger.warning("  -> qm.pop_next() returned None — nothing to play!")
        except Exception as e:
            _playback_logger.error("=== main._on_song_ended FAILED: %s", e, exc_info=True)
            # Stop playback to avoid stuck state; next user action will rebuild queue
            p.stop()
            broadcast("error", {"message": "Playback error", "details": str(e)})
        finally:
            if song_id is None:
                try:
                    _talk_ended_lock.release()
                except RuntimeError:
                    pass
        _playback_logger.debug("=== main._on_song_ended EXIT")

    p.on_song_ended(_on_song_ended)

    # Pre-build recommendation queue on startup (without consuming a song)
    def _prebuild_queue():
        """Start queue rebuild in background after app is ready."""
        time.sleep(2)
        try:
            qm.on_song_started(None)
            print("Queue pre-build complete.")
        except Exception as e:
            print(f"Queue pre-build failed: {e}")

    threading.Thread(target=_prebuild_queue, daemon=True).start()

    # Check TTS engine status (async, non-blocking)
    def _check_tts_voice():
        time.sleep(3)
        try:
            from announcer.voice_manager import get_voice_manager
            vm = get_voice_manager()
            vm.set_database(database)
            vm.initialize()

            # Check OmniVoice availability
            omnivoice_ok = False
            try:
                from announcer.omnivoice_engine import OmniVoiceTTSEngine
                test = OmniVoiceTTSEngine()
                omnivoice_ok = test.is_ready()
                voices = test.get_available_voices()
                if omnivoice_ok:
                    print(f"TTS: OmniVoice ready (GPU available, {len(voices)} voice(s))")
                else:
                    print("TTS: OmniVoice GPU not available — will use Piper fallback")
            except ImportError:
                print("TTS: OmniVoice not installed — using Piper")
            except Exception as e:
                print(f"TTS: OmniVoice check failed: {e} — using Piper")

            # Check Piper fallback
            try:
                from announcer.tts_engine import PiperTTSEngine
                eng = vm.get_engine("piper")
                if isinstance(eng, PiperTTSEngine):
                    available = eng.get_available_voices()
                    if eng.voice_downloaded("amy"):
                        print(f"TTS: Piper voice 'amy' ready ({len(available)} voice(s) available)")
                    else:
                        print("TTS: Piper voice 'amy' not downloaded — attempting download...")
                        ok = eng.load_voice("amy")
                        if ok:
                            print("TTS: Piper voice 'amy' downloaded successfully")
                        else:
                            print("TTS: Piper voice download failed — will use tone fallback")
            except Exception as e:
                print(f"TTS: Piper check failed: {e}")

            # Show active engine
            status = vm.get_status()
            print(f"TTS: Active engine = {status['active_engine']}, voice = {status['active_voice'] or '(none)'}")

        except Exception as e:
            print(f"TTS startup check: {e}")

    threading.Thread(target=_check_tts_voice, daemon=True).start()

    # Start broadcast worker for thread-safe WebSocket messaging
    _set_broadcast_loop(asyncio.get_event_loop())
    app.state._broadcast_worker = asyncio.create_task(_broadcast_worker())
    logger.info("Broadcast worker started")

    yield

    # Cancel broadcast worker on shutdown
    app.state._broadcast_worker.cancel()
    player_engine.get_player().stop()


app = FastAPI(title="Radio", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "file://"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Health Check
# ============================================================

@app.get("/api/health")
def health():
    """Health check / readiness probe endpoint."""
    import os as _os
    db_ok = _os.path.exists(config.DB_PATH)
    return {
        "status": "ok",
        "database": "connected" if db_ok else "missing",
        "version": "0.1.0",
    }


def _chromadb_health_check():
    """Check ChromaDB collections for consistency with SQLite.

    Detects: duplicate IDs, missing embeddings, collection size mismatches.
    Logs warnings for the user but does not block startup.
    """
    try:
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM songs WHERE ingest_phase = 'clustered'"
            ).fetchone()
            sqlite_count = row["cnt"] if row else 0
        finally:
            conn.close()

        client = chromadb.PersistentClient(path=config.CHROMA_PATH)

        # Check 'songs' collection
        try:
            songs_col = client.get_collection("songs")
            songs_count = songs_col.count()
            all_ids = songs_col.get(include=["metadatas"])

            # Detect duplicate IDs
            id_counts = {}
            for id_str in all_ids["ids"]:
                id_counts[id_str] = id_counts.get(id_str, 0) + 1
            duplicates = {sid: cnt for sid, cnt in id_counts.items() if cnt > 1}

            if duplicates:
                logger.warning("ChromaDB 'songs' collection has %d duplicate IDs: %s",
                             len(duplicates), list(duplicates.keys())[:10])
        except Exception as e:
            logger.warning("ChromaDB 'songs' collection check failed: %s", e)
            songs_count = 0
            duplicates = {}

        # Check 'songs_audio' collection
        audio_count = 0
        audio_duplicates = {}
        if config.MUQ_ENABLED:
            try:
                audio_col = client.get_collection(config.MUQ_AUDIO_COLLECTION)
                audio_count = audio_col.count()
                audio_ids = audio_col.get(include=["metadatas"])
                audio_id_counts = {}
                for id_str in audio_ids["ids"]:
                    audio_id_counts[id_str] = audio_id_counts.get(id_str, 0) + 1
                audio_duplicates = {sid: cnt for sid, cnt in audio_id_counts.items() if cnt > 1}
                if audio_duplicates:
                    logger.warning("ChromaDB '%s' collection has %d duplicate IDs: %s",
                                 config.MUQ_AUDIO_COLLECTION, len(audio_duplicates),
                                 list(audio_duplicates.keys())[:10])
            except Exception as e:
                logger.debug("ChromaDB '%s' collection check skipped: %s", config.MUQ_AUDIO_COLLECTION, e)

        # Summary
        logger.info("ChromaDB health: songs=%d (SQLite=%d), audio=%d, duplicates=%d, audio_duplicates=%d",
                    songs_count, sqlite_count, audio_count, len(duplicates), len(audio_duplicates))

        if songs_count == 0 and sqlite_count > 0:
            logger.warning("ChromaDB 'songs' collection is empty but SQLite has %d clustered songs. "
                         "Re-run ingest to populate embeddings.", sqlite_count)

        return {
            "songs_collection": songs_count,
            "sqlite_clustered": sqlite_count,
            "audio_collection": audio_count,
            "duplicates": duplicates,
            "audio_duplicates": audio_duplicates,
            "healthy": len(duplicates) == 0 and len(audio_duplicates) == 0,
        }
    except Exception as e:
        logger.warning("ChromaDB health check failed: %s", e)
        return {"error": str(e), "healthy": False}


@app.get("/api/chroma/health")
def chroma_health():
    """ChromaDB health check endpoint — returns collection stats and detects issues."""
    return _chromadb_health_check()


# WebSocket clients
_connected_clients_lock = threading.Lock()
connected_clients: list[WebSocket] = []


class MoodSetRequest(BaseModel):
    state: str


class VibeLockActivateRequest(BaseModel):
    song_id: int


class CommandRequest(BaseModel):
    text: str


class SeekRequest(BaseModel):
    ms: int = 0


class PlayModeRequest(BaseModel):
    mode: str


class VolumeRequest(BaseModel):
    volume: int = 70


class EqRequest(BaseModel):
    preset: str = "off"


class BlacklistRequest(BaseModel):
    level: str = "never_play"
    reason: str = "user"
    mood: str | None = None
    artist: str | None = None
    cluster_ids: list[int] | None = None


class CollectionCreateRequest(BaseModel):
    name: str
    description: str = ""


class CollectionUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CollectionAddSongRequest(BaseModel):
    song_id: int
    position: int | None = None


class CollectionReorderRequest(BaseModel):
    song_ids: list[int]


class TagsRequest(BaseModel):
    user_tags: list | None = None
    user_notes: str | None = None


class MetadataUpdateRequest(BaseModel):
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    bpm: int | None = None
    duration_secs: float | None = None
    keyscale: str | None = None
    timesignature: str | None = None
    vocal_language: str | None = None
    lyrics: str | None = None
    caption: str | None = None


class DjEffectEligibleRequest(BaseModel):
    eligible: bool


class ReorderRequest(BaseModel):
    order: list[int | None]


class SkipFeedbackRequest(BaseModel):
    reason: str = "just_skip"
    duration: str | None = None  # "24h", "session", "permanent", or None


class SleepTimerRequest(BaseModel):
    minutes: int
    fade_seconds: int = 30
    switch_to_chill: bool = True


_broadcast_queue: asyncio.Queue = asyncio.Queue()
_broadcast_write_lock = asyncio.Lock()
_broadcast_loop = None


def _set_broadcast_loop(loop):
    """Called from the event loop thread to store the running loop reference."""
    global _broadcast_loop
    _broadcast_loop = loop


async def _broadcast_worker():
    """Async worker that serializes all WebSocket writes from the broadcast queue."""
    while True:
        try:
            item = await _broadcast_queue.get()
        except asyncio.CancelledError:
            break
        event_type, data, message = item
        disconnected = []
        with _connected_clients_lock:
            clients_snapshot = list(connected_clients)
        for client in clients_snapshot:
            async with _broadcast_write_lock:
                try:
                    await asyncio.wait_for(client.send_json(message), timeout=3.0)
                except Exception as e:
                    logger.warning("WebSocket send failed: %s", e)
                    disconnected.append(client)
        for client in disconnected:
            with _connected_clients_lock:
                if client in connected_clients:
                    connected_clients.remove(client)
        _broadcast_queue.task_done()


async def _send_websocket_text(websocket: WebSocket, message: str):
    """Serialize direct WebSocket writes with broadcast writes."""
    async with _broadcast_write_lock:
        await websocket.send_text(message)


async def _send_websocket_json(websocket: WebSocket, message: dict):
    """Serialize direct WebSocket writes with broadcast writes."""
    async with _broadcast_write_lock:
        await websocket.send_json(message)


def broadcast(event_type: str, data: dict):
    """Send an event to all connected WebSocket clients (thread-safe)."""
    message = {"event": event_type, **data}
    logger.debug(f"broadcast: event={event_type}, clients={len(connected_clients)}")
    loop = _broadcast_loop
    if loop and not loop.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(_broadcast_queue.put((event_type, data, message)), loop)
        except RuntimeError as e:
            if "closed" not in str(e).lower():
                logger.warning("Broadcast failed: %s", e)
    else:
        logger.debug("Broadcast: no running loop, skipping event=%s", event_type)


@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time event stream for song_ended, queue_updated, mood_changed, etc."""
    await websocket.accept()
    with _connected_clients_lock:
        connected_clients.append(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(connected_clients)}")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await _send_websocket_text(websocket, "pong")
            elif data == "position_request":
                qm = _get_queue_manager()
                with qm._condition:
                    current_id = qm._current_song_id
                if current_id:
                    try:
                        song = database.get_song(current_id)
                        position = 0.0
                        elapsed_ms = 0
                        duration_ms = 0
                        if song:
                            duration_ms = int((song.get("duration_secs") or 0) * 1000)
                        p = _get_player()
                        if p and p.is_playing():
                            elapsed_ms = p.get_time()
                            position = p.get_position()
                        await _send_websocket_json(websocket, {
                            "event": "position_update",
                            "elapsed_ms": elapsed_ms,
                            "duration_ms": duration_ms,
                            "position": position,
                        })
                    except Exception:
                        pass
    except WebSocketDisconnect:
        with _connected_clients_lock:
            if websocket in connected_clients:
                connected_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(connected_clients)}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        with _connected_clients_lock:
            if websocket in connected_clients:
                connected_clients.remove(websocket)


# Global engine instances
_queue_manager = None
_mood_state_machine = None
_singleton_lock = threading.RLock()

# RMS envelope cache for audio-reactive waveform
_rms_cache = {}  # {song_id: [float, ...]} — 100-bin normalized envelope
_current_rms_song_id = None
_rms_lock = threading.Lock()


def _get_current_audio_rms(position_pct: float) -> float:
    """Get interpolated RMS value for current playback position.

    Args:
        position_pct: 0.0 to 1.0 (current playback position as fraction)

    Returns RMS value (0.0-1.0) or 0.5 if no envelope available.
    """
    global _current_rms_song_id
    p = player_engine.get_player()
    song_id = p.get_current_song_id()
    if song_id is None:
        return 0.5
    with _rms_lock:
        if _current_rms_song_id != song_id:
            # Load envelope for new song
            song = database.get_song(song_id)
            if song and song.get("rms_envelope"):
                try:
                    _rms_cache[song_id] = json.loads(song["rms_envelope"])
                except (json.JSONDecodeError, TypeError):
                    _rms_cache[song_id] = None
            else:
                _rms_cache[song_id] = None
            _current_rms_song_id = song_id
        envelope = _rms_cache.get(song_id)
    if not envelope:
        return 0.5
    idx = min(int(position_pct * len(envelope)), len(envelope) - 1)
    return envelope[idx]


def _get_queue_manager():
    global _queue_manager
    if _queue_manager is None:
        with _singleton_lock:
            if _queue_manager is None:
                _queue_manager = queue_engine.QueueManager(
                    player_engine.get_player(),
                    database,
                    broadcast_callback=broadcast,
                    mood_machine=_get_mood_state_machine(),
                )
    return _queue_manager


def _get_mood_state_machine():
    global _mood_state_machine
    if _mood_state_machine is None:
        with _singleton_lock:
            if _mood_state_machine is None:
                _mood_state_machine = mood_engine.MoodStateMachine()
    return _mood_state_machine


def _fetch_song_embeddings(song_id: int) -> tuple:
    """Fetch both text and audio embeddings for a song from ChromaDB.

    Returns (text_embedding, audio_embedding) tuple.
    """
    text_embedding = None
    audio_embedding = None

    try:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        collection = client.get_collection("songs")
        result = collection.get(ids=[str(song_id)], include=["embeddings"])
        text_embedding = result["embeddings"][0] if result.get("embeddings") is not None and len(result["embeddings"]) > 0 and result["embeddings"][0] is not None else None
    except Exception:
        pass

    if config.MUQ_ENABLED:
        try:
            audio_collection = client.get_collection(config.MUQ_AUDIO_COLLECTION)
            audio_result = audio_collection.get(ids=[str(song_id)], include=["embeddings"])
            audio_embedding = audio_result["embeddings"][0] if audio_result.get("embeddings") is not None and len(audio_result["embeddings"]) > 0 and audio_result["embeddings"][0] is not None else None
        except Exception:
            pass

    return text_embedding, audio_embedding


_sleep_timer = None

def _get_sleep_timer():
    global _sleep_timer
    if _sleep_timer is None:
        with _singleton_lock:
            if _sleep_timer is None:
                _sleep_timer = SleepTimer(
                    player_engine.get_player(),
                    _get_mood_state_machine(),
                    broadcast_callback=broadcast,
                )
    return _sleep_timer


# ============================================================
# Ingest Endpoints
# ============================================================

@app.get("/api/ingest/status")
def ingest_status():
    return database.get_ingest_status()


@app.get("/api/ingest/summary")
def ingest_summary():
    total = database.get_total_song_count()
    status = database.get_ingest_status()
    return {"total_songs": total, "by_phase": status}


# ============================================================
# Recovery Score
# ============================================================

@app.post("/api/recovery/calculate")
def calculate_recovery():
    """Recalculate recovery_score for all songs."""
    song_ids = database.get_all_song_ids()
    count = 0
    for sid in song_ids:
        database.calculate_recovery_score(sid)
        count += 1
    return {"status": "done", "songs_updated": count}


@app.post("/api/songs/{song_id}/avg-completion")
def update_song_avg_completion(song_id: int, completion_pct: float = 100.0):
    """Update avg_completion for a song."""
    database.update_avg_completion(song_id, completion_pct)
    return {"status": "updated", "song_id": song_id}


@app.post("/api/ingest/scan")
def ingest_scan():
    from ingest import scanner
    result = scanner.scan_music_dir(config.MUSIC_DIR)
    return {
        "total_files": result["total_files"],
        "unique_files": result["unique_files"],
        "duplicates": result["duplicates"],
        "duplicate_details": result["duplicate_details"],
    }


@app.get("/api/ingest/progress")
def ingest_progress():
    """Return current ingest progress for UI polling."""
    return pipeline.get_ingest_progress()


@app.post("/api/ingest/run")
def ingest_run():
    """Start the ingest pipeline in a background thread."""
    if pipeline.get_ingest_progress()["running"]:
        return {"status": "already_running"}

    def _run():
        try:
            pipeline.run_ingest()
        except Exception as e:
            logger.error("Ingest failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ──────────────────────────────────────────────────────────
# Step-by-step phase execution
# ──────────────────────────────────────────────────────────

PHASE_PREREQUISITES = {
    "raw": None,
    "analyze": lambda: database.get_by_ingest_phase("raw"),
    "normalize": lambda: [
        row["id"] for row in database.get_connection().execute(
            "SELECT id FROM songs WHERE raw_rms IS NOT NULL"
        ).fetchall()
    ],
    "describe": lambda: database.get_by_ingest_phase("analyzed"),
    "embed": lambda: database.get_by_ingest_phase("described"),
    "audio_embed": lambda: database.get_by_ingest_phase("embedded"),
    "cluster": lambda: database.get_by_ingest_phase("embedded"),
    "lyrics_timing": lambda: database.get_connection().execute(
        "SELECT id FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
    ).fetchall(),
}

PHASE_FUNCTIONS = {
    "raw": pipeline.run_phase_raw,
    "analyze": pipeline.run_phase_analyze,
    "normalize": pipeline.run_phase_normalize,
    "describe": pipeline.run_phase_describe,
    "embed": pipeline.run_phase_embed,
    "audio_embed": pipeline.run_phase_embed_audio,
    "cluster": pipeline.run_phase_cluster,
    "lyrics_timing": pipeline.run_phase_lyrics_timing,
}


@app.post("/api/ingest/run-phase/{phase_name}")
def ingest_run_phase(phase_name: str):
    """Run a single ingest phase in a background thread."""
    if phase_name not in PHASE_FUNCTIONS:
        return {"error": f"Unknown phase: {phase_name}. Valid: {list(PHASE_FUNCTIONS.keys())}"}

    progress = pipeline.get_ingest_progress()
    if progress["running"]:
        return {"status": "already_running", "current_phase": progress["phase"]}

    # Check prerequisites — how many songs are ready?
    prereq = PHASE_PREREQUISITES.get(phase_name)
    ready_count = 0
    if prereq:
        result = prereq()
        ready_count = len(result) if result else 0

    if ready_count == 0 and phase_name != "raw":
        return {"status": "no_work", "phase": phase_name, "ready": 0}

    if phase_name == "raw":
        # Raw always has work if music dir has files
        from ingest import scanner
        ready_count = len(scanner.find_audio_files(config.MUSIC_DIR))

    def _run():
        try:
            result = PHASE_FUNCTIONS[phase_name]()
            logger.info("Phase %s complete: %s", phase_name, result)
        except Exception as e:
            logger.error("Phase %s failed: %s", phase_name, e)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "phase": phase_name, "ready": ready_count}


@app.get("/api/ingest/phase-status")
def ingest_phase_status():
    """Return song counts per ingest phase for step-by-step UI."""
    status = database.get_ingest_status()
    total = database.get_total_song_count()
    return {"by_phase": status, "total": total}


@app.post("/api/ingest/reprocess/{song_id}")
def ingest_reprocess(song_id: int):
    """Reset a single song to raw phase for re-processing."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    database.set_ingest_phase(song_id, "raw")
    return {"status": "requeued", "song_id": song_id}


@app.get("/api/ingest/failures")
def ingest_failures():
    """Get songs that failed during ingest."""
    return database.get_ingest_failures()


@app.post("/api/ingest/retry-failures")
def ingest_retry_failures():
    """Reset all failed songs to raw phase for re-processing."""
    failures = database.get_ingest_failures()
    count = 0
    for f in failures:
        if f.get("ingest_phase") in ("raw", "analyzed", "described", "embedded"):
            continue  # not actually failed, just has old error
        database.set_ingest_phase(f["id"], "raw")
        database.set_ingest_error(f["id"], "")
        count += 1
    return {"status": "retried", "count": count}


@app.post("/api/embed/rebuild-audio")
def rebuild_audio_embeddings():
    """Rebuild MuQ-MuLan audio embeddings for all songs.

    One-time migration endpoint. Requires torch + muq packages.
    Runs synchronously — for 342 songs, expect ~3 min on GPU, ~30 min on CPU.
    """
    if not config.MUQ_ENABLED:
        return {"error": "MuQ is disabled (set MUQ_ENABLED=true)"}

    try:
        from llm.muq_engine import is_available
        if not is_available():
            return {"error": "MuQ-MuLan not available (install torch + muq)"}
    except Exception as e:
        return {"error": f"MuQ check failed: {e}"}

    from ingest import embedder
    from tqdm import tqdm

    # Get all songs that have been text-embedded or clustered
    embedded_songs = database.get_by_ingest_phase("embedded")
    clustered_songs = database.get_by_ingest_phase("clustered")
    all_songs = embedded_songs + clustered_songs
    song_ids = [s["id"] for s in all_songs]

    if not song_ids:
        return {"error": "No songs available for audio embedding"}

    print(f"Rebuilding audio embeddings for {len(song_ids)} songs...")
    embedded_ids = embedder.embed_audio_songs(song_ids)
    print(f"Audio-embedded {len(embedded_ids)} songs")

    return {
        "status": "complete",
        "total": len(song_ids),
        "embedded": len(embedded_ids),
    }


@app.post("/api/ingest/reembed-all")
def reembed_all():
    """Re-embed all songs with updated embedding text (folder, collection, neighboring folder).

    Runs synchronously — for 700+ songs, expect ~5-10 min.
    Requires the embedding LLM server to be running.
    """
    from ingest import embedder

    # Get all songs that have been through the pipeline (embedded or clustered)
    embedded_songs = database.get_by_ingest_phase("embedded")
    clustered_songs = database.get_by_ingest_phase("clustered")
    all_songs = embedded_songs + clustered_songs
    song_ids = [s["id"] for s in all_songs]

    if not song_ids:
        return {"error": "No songs available for re-embedding"}

    # Track progress
    _ingest_progress = {"phase": "reembed", "running": True, "total": len(song_ids), "current": 0, "current_song": ""}

    success = 0
    failed = 0
    for i, song_id in enumerate(song_ids):
        song = database.get_song(song_id)
        if not song:
            failed += 1
            continue
        _ingest_progress["current"] = i + 1
        _ingest_progress["current_song"] = f"{song.get('artist', '?')} - {song.get('title', '?')}"
        if embedder.embed_song(song_id, song):
            success += 1
        else:
            failed += 1

    _ingest_progress["running"] = False

    # Invalidate constellation cache so it rebuilds with new embeddings
    qm = _get_queue_manager()
    if hasattr(qm, '_constellations') and qm._constellations:
        qm._constellations.invalidate()

    return {
        "status": "complete",
        "total": len(song_ids),
        "success": success,
        "failed": failed,
    }


@app.post("/api/ingest/reprocess-single/{song_id}")
def ingest_reprocess_single(song_id: int):
    """Re-analyze a single song's audio features and re-normalize the library."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    if not song.get("filepath") or not os.path.exists(song["filepath"]):
        return {"error": "File not found"}

    from ingest import audio as audio_module

    # Re-analyze this one song
    result = audio_module.analyze_audio(song["filepath"])
    gain_db = audio_module.measure_loudness(song["filepath"])
    result["gain_db"] = gain_db
    database.update_song(song_id, **result)

    # Re-normalize the full library
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, raw_rms, crest_factor, spectral_contrast, spectral_rolloff, spectral_flatness "
            "FROM songs WHERE raw_rms IS NOT NULL"
        ).fetchall()
        all_raw = [dict(r) for r in rows]
    finally:
        conn.close()

    if all_raw:
        updates = audio_module.normalize_all_songs(all_raw)
        for upd in updates:
            database.update_song(upd["id"], energy=upd["energy"], valence=upd["valence"])

    return {"status": "reprocessed", "song_id": song_id, "energy": result.get("energy"), "valence": result.get("valence")}


# ============================================================
# Song Endpoints
# ============================================================

@app.get("/api/songs")
def list_songs(
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    sort_by: str = "title",
    sort_dir: str = "asc",
    cluster_id: int | None = None,
    min_energy: float | None = None,
    max_energy: float | None = None,
    min_valence: float | None = None,
    max_valence: float | None = None,
    min_bpm: int | None = None,
    max_bpm: int | None = None,
    min_play_count: int | None = None,
    never_played: bool = False,
    rediscover: bool = False,
    emotional_tag: str | None = None,
    genre_tag: str | None = None,
    missing_metadata: bool = False,
    missing_caption: bool = False,
):
    """List songs with filtering, search, sorting, and pagination."""
    conn = database.get_connection()
    try:
        clauses = []
        params = []

        if missing_metadata:
            clauses.append("(artist IS NULL OR artist = '' OR title IS NULL OR title = '')")

        if missing_caption:
            clauses.append("(ai_description IS NULL OR ai_description = '')")

        if search:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(artist) LIKE ? OR LOWER(lyrics) LIKE ?)")
            params.extend([f"%{search.lower()}%"] * 3)

        if cluster_id is not None:
            clauses.append("cluster_id = ?")
            params.append(cluster_id)

        if min_energy is not None:
            clauses.append("energy >= ?")
            params.append(min_energy)
        if max_energy is not None:
            clauses.append("energy <= ?")
            params.append(max_energy)

        if min_valence is not None:
            clauses.append("valence >= ?")
            params.append(min_valence)
        if max_valence is not None:
            clauses.append("valence <= ?")
            params.append(max_valence)

        if min_bpm is not None:
            clauses.append("bpm >= ?")
            params.append(min_bpm)
        if max_bpm is not None:
            clauses.append("bpm <= ?")
            params.append(max_bpm)

        if min_play_count is not None:
            clauses.append("play_count >= ?")
            params.append(min_play_count)

        if never_played:
            clauses.append("(play_count = 0 OR play_count IS NULL)")

        if rediscover:
            clauses.append("(last_played IS NULL OR last_played < datetime('now', '-30 days'))")

        if emotional_tag:
            clauses.append("emotional_tags LIKE ?")
            params.append(f'%"{emotional_tag}"%')

        if genre_tag:
            clauses.append("genre_tags LIKE ?")
            params.append(f'%"{genre_tag}"%')

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        valid_sort = {
            "title", "artist", "energy", "valence", "bpm", "id",
            "play_count", "skip_count", "avg_completion", "complete_count",
            "last_played", "added_at", "duration_secs", "instrumental",
        }
        if sort_by not in valid_sort:
            sort_by = "title"
        dir_ = "DESC" if sort_dir == "desc" else "ASC"
        if sort_by == "instrumental":
            order = f"ORDER BY (CASE WHEN lyrics IS NULL OR lyrics = '' THEN 0 ELSE (LENGTH(lyrics) - LENGTH(REPLACE(lyrics, ' ', '')) + 1) END) {dir_}"
        else:
            order = f"ORDER BY {sort_by} {dir_}"

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM songs {where} {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM songs {where}",
            params,
        ).fetchone()["cnt"]
        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }
    finally:
        conn.close()


@app.get("/api/songs/{song_id:int}")
def get_song(song_id: int):
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    return song


# ── Lyrics Timing ─────────────────────────────────────────


@app.get("/api/songs/{song_id:int}/lyrics-timing")
def get_lyrics_timing(song_id: int):
    """Return per-line timed lyrics data from WhisperX alignment.

    Returns {lines: [...], language, duration_ms} if timing data exists,
    or {lines: None} if not yet generated.
    """
    path = os.path.join(config.LYRICS_TIMING_DIR, f"{song_id}.json")
    if not os.path.exists(path):
        return {"lines": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"lines": None, "error": str(e)}


@app.get("/api/lyrics-timing/status")
def lyrics_timing_status():
    """Return counts of songs with/without lyrics timing data."""
    conn = database.get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
        ).fetchone()[0]
    finally:
        conn.close()

    timed = 0
    if os.path.exists(config.LYRICS_TIMING_DIR):
        timed = len([f for f in os.listdir(config.LYRICS_TIMING_DIR) if f.endswith(".json")])

    return {
        "total_with_lyrics": total,
        "timed": timed,
        "remaining": total - timed,
    }


@app.get("/api/clusters")
def list_clusters():
    """Return cluster IDs with auto-generated names and song counts."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT cluster_id, COUNT(*) as song_count FROM songs "
            "WHERE cluster_id >= 0 GROUP BY cluster_id ORDER BY cluster_id"
        ).fetchall()

        # Generate cluster names from most common tags per cluster
        clusters = []
        for r in rows:
            cid = r["cluster_id"]
            tag_rows = conn.execute(
                "SELECT emotional_tags, genre_tags FROM songs WHERE cluster_id = ?",
                (cid,),
            ).fetchall()
            tag_counter = {}
            for tr in tag_rows:
                for col in ("emotional_tags", "genre_tags"):
                    try:
                        for tag in json.loads(tr[col] or "[]"):
                            tag_counter[tag] = tag_counter.get(tag, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass
            sorted_tags = sorted(tag_counter, key=tag_counter.get, reverse=True)
            top = sorted_tags[:2]
            name = " / ".join(t.title() for t in top) if top else f"Cluster {cid}"

            clusters.append({
                "id": cid,
                "name": name,
                "count": r["song_count"],
            })
        return clusters
    finally:
        conn.close()


@app.get("/api/chroma/health")
def chroma_health():
    """Check ChromaDB collection for duplicate entries."""
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        import chromadb
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        collection = client.get_collection("songs")
        total = collection.count()
        all_data = collection.get(include=[])
        id_counts = {}
        for id_str in all_data["ids"]:
            id_counts[id_str] = id_counts.get(id_str, 0) + 1
        duplicates = [{"song_id": int(sid), "count": cnt} for sid, cnt in id_counts.items() if cnt > 1]
        return {"total_entries": total, "duplicates": duplicates, "duplicate_count": len(duplicates)}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/chroma/dedup")
def chroma_dedup():
    """Remove duplicate entries from ChromaDB songs collection."""
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        import chromadb
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        collection = client.get_collection("songs")
        all_data = collection.get(include=["embeddings", "metadatas"])
        id_counts = {}
        for id_str in all_data["ids"]:
            id_counts[id_str] = id_counts.get(id_str, 0) + 1
        duplicates = {sid: cnt for sid, cnt in id_counts.items() if cnt > 1}
        removed = {}
        for song_id, count in duplicates.items():
            existing = collection.get(ids=[song_id], include=["embeddings", "metadatas"])
            if existing and existing.get("embeddings") is not None and len(existing["embeddings"]) > 0 and existing["embeddings"][0] is not None:
                collection.delete(ids=[song_id])
                collection.upsert(
                    ids=[song_id],
                    embeddings=[existing["embeddings"][0]],
                    metadatas=existing["metadatas"][0] if existing["metadatas"] else {},
                )
                removed[song_id] = count - 1
        final_count = collection.count()
        return {"removed": removed, "total_removed": sum(removed.values()), "final_count": final_count}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/tags")
def list_tags():
    """Return all unique emotional, genre, and instrument tags across all songs."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT emotional_tags, genre_tags, instrument_tags FROM songs "
            "WHERE ingest_phase = 'clustered'"
        ).fetchall()
        emotional = set()
        genre = set()
        instrument = set()
        for r in rows:
            try:
                for t in json.loads(r["emotional_tags"] or "[]"):
                    if t: emotional.add(t)
            except (json.JSONDecodeError, TypeError):
                pass
            try:
                for t in json.loads(r["genre_tags"] or "[]"):
                    if t: genre.add(t)
            except (json.JSONDecodeError, TypeError):
                pass
            try:
                for t in json.loads(r["instrument_tags"] or "[]"):
                    if t: instrument.add(t)
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "emotional": sorted(emotional),
            "genre": sorted(genre),
            "instrument": sorted(instrument),
        }
    finally:
        conn.close()


@app.get("/api/songs/{song_id:int}/art.jpg")
def get_song_art(song_id: int):
    """Serve album art for a song."""
    from fastapi.responses import FileResponse
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    art_file = song.get("art_file")
    if not art_file:
        return {"error": "No album art"}
    art_path = os.path.join(config.DATA_DIR, art_file)
    if not os.path.exists(art_path):
        return {"error": "Art file not found"}
    return FileResponse(art_path, media_type="image/jpeg")


@app.post("/api/songs/{song_id:int}/extract-art")
def extract_song_art(song_id: int):
    """Extract album art from MP3 ID3 tags."""
    from ingest.tags import extract_album_art
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    filepath = song.get("filepath")
    if not filepath or not os.path.exists(filepath):
        return {"error": "File not found"}
    art_path = extract_album_art(filepath, config.ART_DIR, song_id)
    if art_path:
        database.update_song(song_id, art_file=art_path)
        return {"status": "extracted", "art_file": art_path}
    return {"status": "no_art_found"}


@app.get("/api/songs/neutral")
def neutral_songs(n: int = 20):
    return database.get_neutral_centroid_songs(n)


@app.get("/api/songs/recently-played")
def recently_played(limit: int = 20):
    """Get recently played distinct songs."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT DISTINCT s.id, s.artist, s.title, s.energy, s.valence, s.bpm, s.duration_secs,
                      pe.played_at, pe.signal
               FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE pe.signal IN ('playing', 'completed')
               ORDER BY pe.played_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/songs/prior")
def prior_songs():
    """Get the last 3 distinct played songs (excluding current)."""
    p = player_engine.get_player()
    current_id = p.get_current_song_id() or -1
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT s.id, s.artist, s.title, s.energy, s.valence, s.bpm,
                      s.duration_secs, s.play_count, MAX(pe.played_at) AS last_played
               FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE pe.signal IN ('playing', 'completed')
                 AND s.id != ?
               GROUP BY s.id
               ORDER BY last_played DESC
               LIMIT 3""",
            (current_id or -1,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/songs/{song_id:int}/blacklist")
def blacklist_song(song_id: int, req: BlacklistRequest = None):
    """Add a song to the blacklist with a level."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    level = req.level if req else "never_play"
    reason = req.reason if req else "user"
    mood = req.mood if req else None
    artist = req.artist if req else None
    cluster_ids = req.cluster_ids if req else None

    # Compute expires_at for time-based levels
    expires_at = None
    if level == "avoid_week":
        from datetime import datetime, timedelta
        expires_at = (datetime.now() + timedelta(days=7)).isoformat()
    elif level == "avoid_today":
        from datetime import datetime, timedelta
        expires_at = (datetime.now() + timedelta(days=1)).isoformat()

    database.blacklist_song(song_id, level=level, reason=reason, mood=mood, artist=artist, cluster_ids=json.dumps(cluster_ids) if cluster_ids else None, expires_at=expires_at)
    broadcast("song_blacklisted", {"song_id": song_id, "level": level})
    return {"status": "blacklisted", "song_id": song_id, "level": level}


@app.delete("/api/songs/{song_id:int}/blacklist")
def unblacklist_song(song_id: int):
    """Remove a song from the blacklist."""
    database.unblacklist_song(song_id)
    return {"status": "unblacklisted", "song_id": song_id}


# ============================================================
# Library Verify — orphan detection & file matching
# ============================================================

@app.get("/api/library/verify")
def library_verify():
    """Compare DB songs against filesystem. Returns orphaned DB entries and unregistered MP3 files."""
    import time as _time
    start = _time.time()
    music_dir = config.MUSIC_DIR

    # 1) Orphaned DB entries — songs whose filepath no longer exists on disk
    all_filepaths = database.get_all_filepaths()
    orphaned = []
    for song_id, fp in all_filepaths:
        if not os.path.exists(fp):
            song = database.get_song(song_id)
            if song:
                orphaned.append({
                    "id": song["id"],
                    "filepath": song["filepath"],
                    "artist": song.get("artist") or "",
                    "title": song.get("title") or "",
                    "file_hash": song.get("file_hash") or "",
                    "play_count": song.get("play_count") or 0,
                    "last_played": song.get("last_played"),
                })

    # 2) Unregistered files — MP3s on disk not in DB
    from ingest.scanner import find_audio_files, compute_file_hash
    disk_files = find_audio_files(music_dir)
    known_paths = {fp for _, fp in all_filepaths}
    unregistered = []
    for fp in disk_files:
        if fp not in known_paths:
            fsize = 0
            try:
                fsize = os.path.getsize(fp)
            except OSError:
                pass
            unregistered.append({
                "filepath": fp,
                "filename": os.path.basename(fp),
                "size": fsize,
            })

    elapsed = round(_time.time() - start, 2)
    return {
        "orphaned": orphaned,
        "unregistered": unregistered,
        "stats": {
            "orphaned_count": len(orphaned),
            "unregistered_count": len(unregistered),
            "total_db_songs": len(all_filepaths),
            "total_disk_files": len(disk_files),
            "elapsed": elapsed,
        },
    }


@app.post("/api/library/auto-match")
def library_auto_match():
    """Attempt to match orphaned DB entries to unregistered files by filename similarity."""
    from ingest.metadata import normalize_filename, _find_best_match

    # Get current orphaned and unregistered
    all_filepaths = database.get_all_filepaths()
    known_paths = {fp for _, fp in all_filepaths}

    # Build orphan list
    orphaned = []
    for song_id, fp in all_filepaths:
        if not os.path.exists(fp):
            song = database.get_song(song_id)
            if song:
                orphaned.append({
                    "id": song["id"],
                    "filepath": song["filepath"],
                    "artist": song.get("artist") or "",
                    "title": song.get("title") or "",
                })

    # Build unregistered list indexed by normalized stem
    from ingest.scanner import find_audio_files
    disk_files = find_audio_files(config.MUSIC_DIR)
    unregistered_stems = {}  # normalized_stem -> filepath
    unregistered_by_path = {}
    for fp in disk_files:
        if fp not in known_paths:
            stem = os.path.splitext(os.path.basename(fp))[0]
            norm = normalize_filename(stem)
            unregistered_stems[norm] = fp
            unregistered_by_path[fp] = stem

    # Match each orphan against unregistered files
    matched = []
    matched_file_paths = set()
    unmatched_orphans = []
    for o in orphaned:
        old_stem = os.path.splitext(os.path.basename(o["filepath"]))[0]
        norm_old = normalize_filename(old_stem)
        # Use artist-title if available for better matching
        if o["artist"] and o["title"]:
            search_key = normalize_filename(f"{o['artist']} - {o['title']}")
        else:
            search_key = norm_old
        best_path, best_score = _find_best_match(search_key, unregistered_stems)
        if best_path and best_score >= 0.5:
            # Skip if another DB song already owns this path
            existing = database.get_song_by_filepath(best_path)
            if existing and existing["id"] != o["id"]:
                o_with_reason = dict(o)
                o_with_reason["reason"] = f"filepath owned by song {existing['id']}"
                unmatched_orphans.append(o_with_reason)
                continue
            try:
                from ingest.scanner import compute_file_hash
                new_hash = compute_file_hash(best_path)
                database.update_song(o["id"], filepath=best_path, file_hash=new_hash)
            except Exception as e:
                o_with_reason = dict(o)
                o_with_reason["reason"] = str(e)
                unmatched_orphans.append(o_with_reason)
                continue
            # Remove matched path so it can't be re-matched to another orphan
            norm_best = normalize_filename(os.path.splitext(os.path.basename(best_path))[0])
            unregistered_stems.pop(norm_best, None)
            matched.append({
                "orphan_id": o["id"],
                "old_filepath": o["filepath"],
                "new_filepath": best_path,
                "score": round(best_score, 2),
                "artist": o["artist"],
                "title": o["title"],
            })
            matched_file_paths.add(best_path)
        else:
            unmatched_orphans.append(o)

    # Remaining unregistered files after matches
    remaining_unregistered = []
    for fp in disk_files:
        if fp not in known_paths and fp not in matched_file_paths:
            fsize = 0
            try:
                fsize = os.path.getsize(fp)
            except OSError:
                pass
            remaining_unregistered.append({
                "filepath": fp,
                "filename": os.path.basename(fp),
                "size": fsize,
            })

    return {
        "matched": matched,
        "match_count": len(matched),
        "unmatched_orphans": unmatched_orphans,
        "unregistered_remaining": remaining_unregistered,
        "stats": {
            "orphaned_total": len(orphaned),
            "unregistered_total": len(unregistered_stems),
            "matched": len(matched),
            "unmatched_orphans": len(unmatched_orphans),
            "remaining_files": len(remaining_unregistered),
        },
    }


@app.post("/api/library/confirm-match")
def library_confirm_match(req: dict):
    """Manually confirm a match: pair an orphaned DB entry with an unregistered file."""
    orphan_id = req.get("orphan_id")
    filepath = req.get("filepath")
    if not orphan_id or not filepath:
        return {"error": "orphan_id and filepath required"}, 400
    if not os.path.exists(filepath):
        return {"error": f"File not found: {filepath}"}, 404

    song = database.get_song(orphan_id)
    if not song:
        return {"error": f"Song {orphan_id} not found in database"}, 404

    existing = database.get_song_by_filepath(filepath)
    if existing and existing["id"] != orphan_id:
        return {"error": f"Filepath already owned by song {existing['id']}"}, 409

    from ingest.scanner import compute_file_hash
    new_hash = compute_file_hash(filepath)
    database.update_song(orphan_id, filepath=filepath, file_hash=new_hash)
    return {"status": "matched", "song_id": orphan_id, "new_filepath": filepath}


@app.delete("/api/songs/{song_id:int}")
def library_delete_song(song_id: int):
    """Delete a song from the database and ChromaDB."""
    deleted = database.delete_song(song_id)
    if not deleted:
        return {"error": "Song not found"}, 404

    # Remove from ChromaDB collections
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        for col_name in ["songs", config.MUQ_AUDIO_COLLECTION]:
            try:
                col = client.get_or_create_collection(col_name)
                col.delete(ids=[str(song_id)])
            except Exception:
                pass
    except Exception:
        pass

    return {
        "status": "deleted",
        "song_id": song_id,
        "filepath": deleted.get("filepath"),
    }


@app.post("/api/library/add-unregistered")
def library_add_unregistered(req: dict):
    """Add an unregistered MP3 file as a new song."""
    filepath = req.get("filepath")
    if not filepath:
        return {"error": "filepath required"}, 400
    if not os.path.exists(filepath):
        return {"error": f"File not found: {filepath}"}, 404

    # Check if already in DB
    existing = database.get_song_by_filepath(filepath)
    if existing:
        return {"error": "File already in database", "song_id": existing["id"]}, 409

    from ingest.scanner import compute_file_hash, parse_filename
    file_hash = compute_file_hash(filepath)
    parsed = parse_filename(filepath)

    database.add_song(filepath, file_hash)
    # Fetch the newly added song to get its ID
    new_song = database.get_song_by_filepath(filepath)
    if new_song:
        database.update_song(
            new_song["id"],
            artist=parsed.get("artist"),
            title=parsed.get("title"),
            ingest_phase="raw",
        )

    return {
        "status": "added",
        "song_id": new_song["id"] if new_song else None,
        "artist": parsed.get("artist"),
        "title": parsed.get("title"),
    }


@app.post("/api/library/delete-all-orphaned")
def library_delete_all_orphaned():
    """Delete all orphaned DB entries (songs whose files are missing)."""
    all_filepaths = database.get_all_filepaths()
    orphan_ids = []
    for song_id, fp in all_filepaths:
        if not os.path.exists(fp):
            orphan_ids.append(song_id)

    if not orphan_ids:
        return {"status": "none", "deleted": 0}

    deleted = database.delete_songs_batch(orphan_ids)

    # Remove from ChromaDB
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        for col_name in ["songs", config.MUQ_AUDIO_COLLECTION]:
            try:
                col = client.get_or_create_collection(col_name)
                col.delete(ids=[str(sid) for sid in orphan_ids])
            except Exception:
                pass
    except Exception:
        pass

    return {"status": "deleted", "deleted": len(deleted), "song_ids": orphan_ids}


# ============================================================
# Collections
# ============================================================

@app.get("/api/collections")
def list_collections():
    """List all collections."""
    return {"collections": database.get_collections()}


@app.post("/api/collections")
def create_collection(req: CollectionCreateRequest):
    """Create a new collection."""
    cid = database.create_collection(req.name, req.description)
    return {"status": "created", "collection_id": cid}


@app.put("/api/collections/{collection_id:int}")
def update_collection(collection_id: int, req: CollectionUpdateRequest):
    """Update a collection's metadata."""
    database.update_collection(collection_id, name=req.name, description=req.description)
    return {"status": "updated"}


@app.delete("/api/collections/{collection_id:int}")
def delete_collection(collection_id: int):
    """Delete a collection."""
    database.delete_collection(collection_id)
    return {"status": "deleted"}


@app.get("/api/collections/{collection_id:int}/songs")
def get_collection_songs(collection_id: int):
    """Get songs in a collection."""
    songs = database.get_collection_songs(collection_id)
    return {"songs": songs}


@app.post("/api/collections/{collection_id:int}/songs")
def add_to_collection(collection_id: int, req: CollectionAddSongRequest):
    """Add a song to a collection."""
    database.add_to_collection(collection_id, req.song_id, req.position)
    return {"status": "added"}


@app.delete("/api/collections/{collection_id:int}/songs/{song_id:int}")
def remove_from_collection(collection_id: int, song_id: int):
    """Remove a song from a collection."""
    database.remove_from_collection(collection_id, song_id)
    return {"status": "removed"}


@app.put("/api/collections/{collection_id:int}/reorder")
def reorder_collection(collection_id: int, req: CollectionReorderRequest):
    """Reorder songs in a collection."""
    database.reorder_collection(collection_id, req.song_ids)
    return {"status": "reordered"}


@app.get("/api/blacklist")
def get_blacklist():
    """Get all blacklisted songs."""
    return database.get_blacklisted()


@app.put("/api/songs/{song_id:int}/tags")
def update_song_tags(song_id: int, req: TagsRequest):
    """Update user tags and notes for a song."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    updates = {}
    if req.user_tags is not None:
        updates["user_tags"] = json.dumps(req.user_tags) if isinstance(req.user_tags, list) else req.user_tags
    if req.user_notes is not None:
        updates["user_notes"] = req.user_notes
    if updates:
        database.update_song(song_id, **updates)
    return {"status": "updated", "song_id": song_id}


@app.get("/api/songs/{song_id:int}/metadata-source")
def get_metadata_source(song_id: int):
    """Get current metadata from source files (ID3 tags + acestep JSON)."""
    from ingest.metadata import load_all_metadata

    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}

    filepath = song.get("filepath", "")
    result = {"db": song}

    # Read ID3 tags from MP3
    try:
        from ingest.tags import extract_tags
        id3 = extract_tags(filepath)
        result["id3"] = {k: v for k, v in id3.items() if v}
    except Exception as e:
        result["id3_error"] = str(e)

    # Read acestep JSON
    try:
        all_meta = load_all_metadata()
        from ingest.metadata import get_metadata_for_mp3
        ace = get_metadata_for_mp3(filepath, all_meta)
        if ace:
            result["acestep"] = {k: v for k, v in ace.items() if v}
    except Exception as e:
        result["acestep_error"] = str(e)

    return result


@app.put("/api/songs/{song_id:int}/metadata")
def update_song_metadata(song_id: int, req: MetadataUpdateRequest):
    """Update metadata for a song and persist to source files (MP3 ID3 + acestep JSON).

    If artist or title changes, the MP3 file and acestep JSON are renamed
    to match the new name, and the DB filepath is updated.

    Writes to three places:
    1. MP3 ID3 tags (artist, title, album)
    2. Acestep JSON (bpm, duration, keyscale, lyrics, caption, etc.)
    3. Database (all fields + filepath if renamed)
    """
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}

    # Build DB update dict
    db_updates = {}
    if req.artist is not None:
        db_updates["artist"] = req.artist.strip() or None
    if req.title is not None:
        db_updates["title"] = req.title.strip() or None
    if req.album is not None:
        db_updates["album"] = req.album.strip() or None
    if req.bpm is not None:
        db_updates["bpm"] = req.bpm
    if req.duration_secs is not None:
        db_updates["duration_secs"] = req.duration_secs
    if req.keyscale is not None:
        db_updates["keyscale"] = req.keyscale.strip() or None
    if req.timesignature is not None:
        db_updates["timesignature"] = req.timesignature.strip() or None
    if req.vocal_language is not None:
        db_updates["vocal_language"] = req.vocal_language.strip() or None
    if req.lyrics is not None:
        db_updates["lyrics"] = req.lyrics or None
    if req.caption is not None:
        db_updates["ai_description"] = req.caption.strip() or None

    # Check if artist or title changed (triggers file rename)
    new_artist = (req.artist or "").strip()
    new_title = (req.title or "").strip()
    old_artist = (song.get("artist") or "").strip()
    old_title = (song.get("title") or "").strip()
    name_changed = (new_artist != old_artist) or (new_title != old_title)

    # Persist to source files
    filepath = song.get("filepath", "")
    id3_result = {"status": "skipped"}
    ace_result = {"status": "skipped"}
    rename_result = {"mp3": {"status": "skipped"}, "json": {"status": "skipped"}}

    if filepath:
        # Rename MP3 and JSON files if artist/title changed
        if name_changed:
            try:
                from ingest.metadata_writer import rename_song_files
                rename_result = rename_song_files(filepath, new_artist, new_title)
                # Update filepath to the new path
                new_filepath = rename_result.get("mp3", {}).get("new_path")
                if new_filepath:
                    db_updates["filepath"] = new_filepath
                    from ingest.scanner import compute_file_hash
                    db_updates["file_hash"] = compute_file_hash(new_filepath)
            except Exception as e:
                logger.error("Failed to rename song files: %s", e)
                rename_result = {"mp3": {"status": "error", "reason": str(e)}, "json": {"status": "error", "reason": str(e)}}

        # Determine the current filepath (may have been updated by rename)
        current_filepath = db_updates.get("filepath", filepath)

        # Write ID3 tags
        try:
            from ingest.metadata_writer import write_id3_tags
            id3_result = write_id3_tags(
                current_filepath,
                artist=req.artist,
                title=req.title,
                album=req.album,
            )
        except Exception as e:
            id3_result = {"error": str(e)}

        # Write acestep JSON
        try:
            from ingest.metadata_writer import write_acestep_json
            ace_result = write_acestep_json(
                current_filepath,
                caption=req.caption,
                lyrics=req.lyrics,
                bpm=req.bpm,
                duration=req.duration_secs,
                keyscale=req.keyscale,
                timesignature=req.timesignature,
                vocal_language=req.vocal_language,
            )
        except Exception as e:
            ace_result = {"error": str(e)}

    # Update DB (including filepath if renamed)
    if db_updates:
        database.update_song(song_id, **db_updates)

    updated_song = database.get_song(song_id)
    return {
        "status": "updated",
        "song": updated_song,
        "id3": id3_result,
        "acestep": ace_result,
        "rename": rename_result,
    }


# ── DJ Effects ──────────────────────────────────────────────


@app.put("/api/songs/{song_id:int}/dj-effect-eligible")
def update_dj_effect_eligible(song_id: int, req: DjEffectEligibleRequest):
    """Toggle DJ effect eligibility for a song."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    database.update_song(song_id, dj_effect_eligible=1 if req.eligible else 0)
    return {"status": "updated", "song_id": song_id, "eligible": req.eligible}


@app.get("/api/dj-effects/presets")
def get_dj_effect_presets():
    """List available DJ effect presets."""
    from engine.dj_effects import get_available_presets
    return get_available_presets()


@app.get("/api/dj-effects/cache-info")
def get_dj_effect_cache_info():
    """Get DJ effect cache statistics."""
    from engine.dj_effects import get_cache_stats
    return get_cache_stats()


@app.post("/api/dj-effects/generate/{song_id:int}")
def generate_dj_effect(song_id: int, body: dict):
    """Manually generate a DJ effect for a specific song."""
    preset_name = body.get("preset", database.get_setting("dj_effect_preset") or "slowed_reverb")
    from engine.dj_effects import get_or_generate
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    filepath = song.get("filepath")
    if not filepath:
        return {"error": "No filepath for song"}
    import os as _os
    if not _os.path.exists(filepath):
        return {"error": "File not found"}
    try:
        effect_path = get_or_generate(filepath, song_id, preset_name)
        if effect_path:
            return {"status": "ok", "filepath": effect_path, "preset": preset_name}
        return {"error": "Effect generation failed"}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Play Endpoints
# ============================================================

_current_session_id = None
_session_lock = threading.Lock()


def _ensure_session():
    global _current_session_id
    with _session_lock:
        if not _current_session_id:
            msm = _get_mood_state_machine()
            _current_session_id = database.create_session(initial_mood=msm.get_state())
        return _current_session_id


def _close_current_session():
    global _current_session_id
    with _session_lock:
        if _current_session_id:
            database.close_session(_current_session_id)
            _current_session_id = None


@app.get("/api/play/status")
def play_status():
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    song_id = p.get_current_song_id()
    current_song = None
    if song_id:
        raw = database.get_song(song_id)
        if raw:
            current_song = dict(raw)

    # Get queue info
    with qm._condition:
        queue_preview = []
        for item in qm._get_display_queue(_locked=True)[:4]:
            if item.get("type") == "talk_segment":
                queue_preview.append({
                    "type": "talk_segment",
                    "segment_type": item.get("segment_type"),
                    "reason": item.get("reason", ""),
                    "slot": item.get("slot"),
                    "duration_secs": item.get("duration_secs", 0),
                    "song_id": None,
                    "artist": "",
                    "title": "",
                })
            else:
                queue_preview.append({
                    "song_id": item.get("song_id"),
                    "slot": item.get("slot"),
                    "reason": item.get("reason", ""),
                    "artist": item.get("artist", ""),
                    "title": item.get("title", ""),
                })

    # Get archetype
    archetype_name, archetype_metrics = archetype_engine.detect_archetype()

    current_mode = database.get_setting("current_mode") or "radio"

    # Reliable position: use VLC position (0-1) * known db duration as fallback
    vlc_pos = p.get_position()
    vlc_time = p.get_time()
    vlc_length = p.get_length()
    song_duration_ms = (current_song.get("duration_secs") or 0) * 1000 if current_song else 0
    # If VLC length is unavailable/unreliable, use db duration
    if vlc_length <= 0 and song_duration_ms > 0:
        vlc_length = song_duration_ms
    # If VLC time is unavailable but position is known, compute from position
    if (vlc_time <= 0 or vlc_length <= 0) and vlc_pos > 0 and song_duration_ms > 0:
        vlc_time = int(vlc_pos * song_duration_ms)
        vlc_length = song_duration_ms

    # Check if a talk segment is pending (for frontend display)
    talk_segment_active = None
    with qm._condition:
        if qm._pending_talk_segments:
            seg = qm._pending_talk_segments[0]
            talk_segment_active = {
                "segment_type": seg.get("segment_type"),
                "reason": seg.get("reason", ""),
                "duration_secs": seg.get("duration_secs", 0),
            }

    # Get audio-reactive RMS from cached envelope
    position_pct = vlc_pos if vlc_pos > 0 else (vlc_time / max(vlc_length, 1))
    audio_rms = _get_current_audio_rms(position_pct)

    return {
        "is_playing": p.is_playing(),
        "current_song": current_song,
        "mood_state": msm.get_state(),
        "vibe_lock": msm.get_vibe_lock_info(),
        "position": vlc_pos,
        "position_ms": vlc_time,
        "duration_ms": vlc_length,
        "queue": queue_preview,
        "skip_streak": qm._skip_streak,
        "archetype": archetype_name,
        "archetype_metrics": archetype_metrics,
        "current_mode": current_mode,
        "audio_rms": audio_rms,
        "talk_segment_active": talk_segment_active,
        "talk_playing": p.is_talk_playing(),
        "hearted_this_play": database.has_signal_since_last_play(song_id, "hearted") if song_id else False,
        "thumbed_down_this_play": database.has_signal_since_last_play(song_id, "thumbed_down") if song_id else False,
    }


@app.post("/api/play/{song_id:int}")
def play_song(song_id: int):
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    filepath = song["filepath"]
    if not filepath or not os.path.exists(filepath):
        return {"error": "File not found", "filepath": filepath}

    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    session_id = _ensure_session()

    # Record play event
    database.record_play_event(
        song_id=song_id,
        session_id=session_id,
        signal="playing",
        mood_state=msm.get_state(),
    )

    success = p.play_song(song_id, filepath, session_id)
    if not success:
        return {"error": "Failed to start playback"}

    # Notify queue manager
    qm.on_song_started(song_id, session_id)

    broadcast("song_started", {
        "song_id": song_id,
        "session_id": session_id,
        "elapsed_ms": 0,
    })
    return {"status": "playing", "song_id": song_id}


@app.post("/api/play/pause")
def pause_playback():
    """Pause playback (not toggle). Does nothing if already paused."""
    p = player_engine.get_player()
    if p.get_state() == "playing":
        p.pause()
    return {"status": p.get_state()}


@app.post("/api/play/resume")
def resume_playback():
    """Resume playback or start a new song.

    If paused: resume normally (VLC unpause).
    If stopped with current song: restart the song.
    If stopped without current song: pop from queue and play.
    Returns current state and action taken.
    """
    p = player_engine.get_player()
    state = p.get_state()

    if state == "playing":
        return {"status": "playing", "action": "already_playing"}

    if state == "paused":
        qm = _get_queue_manager()
        # Restore _current_song_id from queue manager in case it was cleared
        paused_song_id = p.get_current_song_id()
        if not paused_song_id:
            paused_song_id = qm._current_song_id
        if paused_song_id:
            song = database.get_song(paused_song_id)
            if song and song.get("filepath") and os.path.exists(song["filepath"]):
                session_id = qm._current_session_id or _ensure_session()
                p._current_song_id = paused_song_id
                p._current_session_id = session_id
                p.resume()
                broadcast("song_started", {"song_id": paused_song_id, "session_id": session_id})
                return {"status": "playing", "action": "resumed", "song_id": paused_song_id}
        # Fallback: try to restart with queue manager's song
        fallback_song_id = qm._current_song_id
        if fallback_song_id:
            song = database.get_song(fallback_song_id)
            if song and song.get("filepath") and os.path.exists(song["filepath"]):
                session_id = qm._current_session_id or _ensure_session()
                p.play_song(fallback_song_id, song["filepath"], session_id)
                qm.on_song_started(fallback_song_id, session_id)
                broadcast("song_started", {"song_id": fallback_song_id, "session_id": session_id, "elapsed_ms": 0})
                return {"status": "playing", "action": "resumed", "song_id": fallback_song_id}
        # Nothing to resume — pop from queue
        pick = qm.pop_next()
        if pick:
            if pick.get("type") == "talk_segment":
                qm.play_next(pick)
            else:
                session_id = _ensure_session()
                database.record_play_event(
                    song_id=pick["song_id"],
                    session_id=session_id,
                    signal="playing",
                    queue_slot=pick.get("slot"),
                    mood_state=_get_mood_state_machine().get_state(),
                )
                qm.play_next(pick)
                qm.on_song_started(pick["song_id"], session_id)
                broadcast("song_started", {"song_id": pick["song_id"], "session_id": session_id, "elapsed_ms": 0})
            return {"status": "playing", "action": "resumed", "song_id": pick.get("song_id")}
        return {"status": "stopped", "action": "nothing_to_play"}

    # Stopped — try to restart current song
    song_id = p.get_current_song_id()
    if not song_id:
        qm = _get_queue_manager()
        song_id = qm._current_song_id
    if song_id:
        song = database.get_song(song_id)
        if song and song.get("filepath") and os.path.exists(song["filepath"]):
            session_id = _ensure_session()
            success = p.play_song(song_id, song["filepath"], session_id)
            if success:
                qm = _get_queue_manager()
                qm.on_song_started(song_id, session_id)
                broadcast("song_started", {"song_id": song_id, "session_id": session_id, "elapsed_ms": 0})
                return {"status": "playing", "action": "restarted", "song_id": song_id}

    # Stopped and no current song — pop from queue
    qm = _get_queue_manager()
    pick = qm.pop_next()
    if pick:
        if pick.get("type") == "talk_segment":
            qm.play_next(pick)
        else:
            session_id = _ensure_session()
            database.record_play_event(
                song_id=pick["song_id"],
                session_id=session_id,
                signal="playing",
                queue_slot=pick.get("slot"),
                mood_state=_get_mood_state_machine().get_state(),
            )
            qm.play_next(pick)
            qm.on_song_started(pick["song_id"], session_id)
            broadcast("song_started", {"song_id": pick["song_id"], "session_id": session_id, "elapsed_ms": 0})
        return {"status": "playing", "action": "new_song", "song_id": pick.get("song_id")}

    return {"status": "stopped", "action": "nothing_to_play"}


@app.post("/api/play/seek")
def play_seek(req: SeekRequest):
    """Seek to a position in milliseconds."""
    p = player_engine.get_player()
    p.seek(req.ms)
    return {"status": "seeked", "ms": req.ms}


@app.post("/api/play/volume")
def set_volume(req: VolumeRequest):
    """Set player volume (0-100)."""
    p = player_engine.get_player()
    p.set_volume(req.volume)
    return {"status": "volume_set", "volume": req.volume}


@app.post("/api/play/volume-normalization")
def toggle_volume_normalization(enabled: bool = Query(True)):
    """Toggle volume normalization (ReplayGain-style)."""
    p = player_engine.get_player()
    p.set_normalization_enabled(enabled)
    database.set_setting("volume_normalization", "true" if enabled else "false")
    return {"status": "set", "enabled": enabled}


@app.post("/api/play/eq")
def set_eq(req: EqRequest):
    """Set equalizer preset."""
    p = player_engine.get_player()
    p.set_eq_preset(req.preset)
    database.set_setting("eq_preset", req.preset)
    return {"status": "eq_set", "preset": req.preset}


@app.get("/api/play/eq")
def get_eq():
    """Get current equalizer preset."""
    p = player_engine.get_player()
    return {"preset": p.get_eq_preset() or "off"}


@app.get("/api/play/mode")
def get_play_mode():
    """Get current play mode (ai/shuffle/sequential)."""
    mode = database.get_setting("play_mode") or "ai"
    return {"mode": mode}


@app.post("/api/play/mode")
def set_play_mode(req: PlayModeRequest):
    """Set play mode (ai/shuffle/sequential)."""
    if req.mode not in ("ai", "shuffle", "sequential"):
        return {"error": "Invalid mode. Must be 'ai', 'shuffle', or 'sequential'"}
    database.set_setting("play_mode", req.mode)
    # Clear queue so the next rebuild picks up the new mode
    qm = _get_queue_manager()
    with qm._condition:
        qm._queue.clear()
    broadcast("play_mode_changed", {"mode": req.mode})
    return {"status": "set", "mode": req.mode}


@app.post("/api/play/next")
def play_next():
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    song_id = p.get_current_song_id()

    if song_id:
        session_id = p._current_session_id if hasattr(p, '_current_session_id') else _current_session_id
        elapsed_ms = p.get_time()
        total_ms = p.get_length()
        pct = round((elapsed_ms / max(1, total_ms)) * 100, 1)
        # Last 10% = treat as completed, not a skip
        if pct >= 90.0:
            signal = "completed"
            pct = 100.0
        else:
            signal = "skip_early" if elapsed_ms < 30000 else "skip_late"
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal=signal,
            mood_state=msm.get_state(),
            completion_pct=pct,
            skip_at_secs=elapsed_ms / 1000,
        )
        song = database.get_song(song_id)
        qm.record_action("skip", song_id, song, signal_type=signal)
        broadcast("song_skipped", {"song_id": song_id})

    p.stop()
    return {"status": "next"}


@app.post("/api/play/heart")
def heart_current():
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    song_id = p.get_current_song_id()
    session_id = p._current_session_id if hasattr(p, '_current_session_id') else _current_session_id
    if song_id:
        if database.has_signal_since_last_play(song_id, "hearted"):
            return {"status": "already_hearted", "song_id": song_id}
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal="hearted",
            mood_state=msm.get_state(),
        )
        database.increment_replay_count(song_id)
        qm.record_heart()
        broadcast("song_hearted", {"song_id": song_id})
    return {"status": "hearted", "song_id": song_id}


@app.post("/api/play/thumbs-down")
def thumbs_down_current():
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    song_id = p.get_current_song_id()
    session_id = p._current_session_id if hasattr(p, '_current_session_id') else _current_session_id
    if song_id:
        if database.has_signal_since_last_play(song_id, "thumbed_down"):
            return {"status": "already_thumbed_down", "song_id": song_id}
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal="thumbed_down",
            mood_state=msm.get_state(),
        )
        qm.reset_skip_streak()
        song = database.get_song(song_id)
        qm.record_action("thumbs_down", song_id, song)
        broadcast("song_thumbed_down", {"song_id": song_id})
        undo_state = qm.get_undo_state()
        return {"status": "thumbed_down", "song_id": song_id, "undo": undo_state}
    return {"status": "thumbed_down", "song_id": song_id}


@app.post("/api/play/stop")
def stop_playback():
    p = player_engine.get_player()
    p.stop()
    _close_current_session()
    return {"status": "stopped"}


# ============================================================
# Queue Endpoints
# ============================================================

@app.get("/api/queue")
def get_queue():
    qm = _get_queue_manager()
    display_queue = qm._get_display_queue()

    # Add mood match scores when mood is active and MuQ is available
    mood_state = qm._mood.get_state()
    if mood_state and mood_state != "neutral" and config.MUQ_ENABLED:
        try:
            from llm.muq_engine import is_available
            if is_available():
                centroid = qm._mood.get_mood_centroid(mood_state)
                if centroid:
                    song_ids = [str(item["song_id"]) for item in display_queue if "song_id" in item]
                    if song_ids:
                        import chromadb
                        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
                        audio_collection = client.get_collection(config.MUQ_AUDIO_COLLECTION)
                        data = audio_collection.get(ids=song_ids, include=["embeddings"])
                        emb_map = {}
                        if data and data.get("ids"):
                            for i in range(len(data["ids"])):
                                emb_map[data["ids"][i]] = data["embeddings"][i]

                        from engine.utils import cosine_similarity
                        for item in display_queue:
                            sid = str(item.get("song_id", ""))
                            if sid in emb_map:
                                sim = cosine_similarity(centroid, emb_map[sid])
                                item["mood_match"] = round(sim * 100)
                            else:
                                item["mood_match"] = None
        except Exception:
            pass  # Best effort — don't fail the queue endpoint

    return display_queue


@app.get("/api/queue/status")
def get_queue_status():
    """Return queue status including rebuilding state."""
    qm = _get_queue_manager()
    with qm._condition:
        display_queue = qm._get_display_queue(_locked=True)
        return {
            "rebuilding": qm._rebuilding,
            "queue_size": len(display_queue),
            "queue": display_queue,
            "ai_queue_size": len(qm._queue),
            "manual_queue_size": len(qm._manual_queue),
            "pending_talk_segments": len(qm._pending_talk_segments),
        }


@app.post("/api/queue/pop-next")
def queue_pop_next():
    """Pop the next song from the recommendation queue."""
    qm = _get_queue_manager()
    pick = qm.pop_next()
    if pick:
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
    return pick or {"error": "No recommendation available"}


@app.post("/api/queue/peek-next")
def queue_peek_next():
    """Peek at the next song without removing it."""
    qm = _get_queue_manager()
    with qm._condition:
        display_queue = qm._get_display_queue(_locked=True)
        if display_queue:
            return display_queue[0]
        return {"error": "Queue is empty"}


@app.post("/api/queue/clear")
def queue_clear():
    """Clear the recommendation queue and any pending talk segments."""
    qm = _get_queue_manager()
    qm.clear_queue()
    broadcast("queue_updated", {"queue": []})
    return {"status": "queue_cleared"}


@app.post("/api/queue/rebuild")
def queue_rebuild():
    """Force a queue rebuild in the background."""
    qm = _get_queue_manager()
    with qm._condition:
        qm._queue.clear()
        qm._rebuilding = True
        qm._rebuild_gen += 1
    threading.Thread(target=qm._rebuild_queue, daemon=True).start()
    return {"status": "rebuild_started"}


@app.post("/api/queue/add/{song_id:int}")
def queue_add(song_id: int):
    """Add a song to the manual queue (survives AI rebuilds)."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    qm = _get_queue_manager()
    res = qm.add_manual([song_id])
    if res["added"]:
        return {"status": "added", "song_id": song_id}
    return {"status": "already_queued", "song_id": song_id}


@app.delete("/api/queue/remove/{song_id:int}")
def queue_remove(song_id: int):
    """Remove a song from the manual queue."""
    qm = _get_queue_manager()
    removed = qm.remove_manual(song_id)
    if removed:
        return {"status": "removed", "song_id": song_id}
    return {"error": "Not in manual queue"}


@app.put("/api/queue/reorder")
def queue_reorder(req: ReorderRequest):
    """Reorder the queue by song_id list."""
    order = req.order
    if not order:
        return {"error": "No order provided"}
    qm = _get_queue_manager()
    display_queue = qm.reorder_display_queue(order)
    broadcast("queue_updated", {"queue": display_queue})
    return {"status": "reordered", "queue": display_queue}


@app.get("/api/queue/history")
def queue_history(limit: int = 30):
    """Get recent queue history (what was played in what order)."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT qh.*, s.artist, s.title
               FROM queue_history qh
               LEFT JOIN songs s ON qh.song_id = s.id
               ORDER BY qh.played_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/queue/skip")
def queue_skip():
    """Skip the current song and record a skip event."""
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()
    song_id = p.get_current_song_id()

    is_completed = False
    if song_id:
        session_id = p._current_session_id if hasattr(p, '_current_session_id') else _current_session_id
        elapsed_ms = p.get_time()
        total_ms = p.get_length()
        pct = round((elapsed_ms / max(1, total_ms)) * 100, 1)
        # Last 10% = treat as completed, not a skip
        if pct >= 90.0:
            signal = "completed"
            pct = 100.0
            is_completed = True
        else:
            signal = "skip_early" if elapsed_ms < 30000 else "skip_late"
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal=signal,
            mood_state=msm.get_state(),
            completion_pct=pct,
            skip_at_secs=elapsed_ms / 1000,
        )
        # Track for potential undo
        song = database.get_song(song_id)
        qm.record_action("skip", song_id, song, signal_type=signal)
        broadcast("song_skipped", {"song_id": song_id})

    # Only increment skip streak for actual skips (not near-complete listens)
    if not is_completed:
        qm._skip_streak += 1

    # Check for auto mood shift on skip streak (only for real skips)
    mood_shifted = False
    if not is_completed and qm._skip_streak >= 3:
        new_state = msm.check_auto_mood_shift([], qm._skip_streak)
        if new_state:
            msm.set_state(new_state)
            mood_shifted = True
            broadcast("mood_changed", {"state": new_state, "reason": f"Skip streak ({qm._skip_streak})"})
            database.record_mood_change(_current_session_id, new_state, source="auto")

    # If mood shifted, don't stop playback — let the current song finish so the
    # next queue rebuild picks songs matching the new mood.
    if not mood_shifted:
        p.stop()

    # After 5+ skips, use recovery song instead of normal queue
    pick = None
    if qm._skip_streak >= 5:
        from engine import recovery
        exclude = {song_id} if song_id else set()
        exclude |= qm._recently_recommended_ids
        recovery_song, r_type = recovery.get_recovery_song(
            current_song_id=song_id, exclude_ids=exclude
        )
        if recovery_song:
            pick = {
                "song_id": recovery_song["id"],
                "slot": "recovery",
                "reason": f"Back to something reliable after {qm._skip_streak} skips.",
                "artist": recovery_song.get("artist", ""),
                "title": recovery_song.get("title", ""),
            }
            broadcast("song_skipped", {"song_id": song_id, "recovery": True})

    if not pick:
        pick = qm.pop_next()

    # Build response
    undo_state = qm.get_undo_state()
    response = {
        "status": "skipped",
        "skip_streak": qm._skip_streak,
        "show_feedback": qm._skip_streak >= 2,
        "undo": undo_state,
    }
    if mood_shifted:
        response["mood_shift"] = True
        response["next"] = pick
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        return response

    if pick:
        if pick.get("type") == "talk_segment":
            qm.play_next(pick)
        else:
            session_id = _ensure_session()
            database.record_play_event(
                song_id=pick["song_id"],
                session_id=session_id,
                signal="playing",
                queue_slot=pick.get("slot"),
                mood_state=msm.get_state(),
            )
            qm.play_next(pick)
            qm.on_song_started(pick["song_id"], session_id)
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        response["next"] = pick
        return response
    return response


@app.post("/api/queue/play-next")
def queue_play_next():
    """Play the next song from the recommendation queue."""
    qm = _get_queue_manager()
    pick = qm.pop_next()
    if pick:
        if pick.get("type") == "talk_segment":
            qm.play_next(pick)
        else:
            session_id = _ensure_session()
            database.record_play_event(
                song_id=pick["song_id"],
                session_id=session_id,
                signal="playing",
                queue_slot=pick.get("slot"),
                mood_state=_get_mood_state_machine().get_state(),
            )
            qm.play_next(pick)
            qm.on_song_started(pick["song_id"], session_id)
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        return {"status": "playing", "next": pick}
    return {"error": "No recommendation available"}


@app.post("/api/queue/replay")
def queue_replay():
    """Replay the current song."""
    p = player_engine.get_player()
    qm = _get_queue_manager()
    song_id = p.get_current_song_id()
    if song_id:
        song = database.get_song(song_id)
        filepath = song["filepath"]
        if filepath and os.path.exists(filepath):
            p.play_song(song_id, filepath)
            qm.record_replay()
            database.increment_replay_count(song_id)
            broadcast("song_replayed", {"song_id": song_id})
            return {"status": "replaying", "song_id": song_id}
    return {"error": "No song to replay"}


@app.get("/api/queue/skip-streak")
def get_skip_streak():
    """Get the current skip streak count."""
    qm = _get_queue_manager()
    return {"skip_streak": qm._skip_streak}


# ── Skip Feedback ───────────────────────────────────────────
@app.post("/api/queue/skip-feedback")
def submit_skip_feedback(body: SkipFeedbackRequest):
    """Record skip feedback reason from the user.
    
    Supports temporal duration: "24h", "session", "permanent", or None (no suppression).
    """
    qm = _get_queue_manager()
    session_id = _current_session_id
    if not session_id:
        return {"status": "no_session"}

    song_id = qm._current_song_id

    # Record feedback with duration
    database.record_skip_feedback(session_id, qm._skip_streak, body.reason, body.duration)

    # Apply temporal suppression based on duration
    if song_id and body.duration:
        if body.duration == "24h":
            database.add_song_suppression(song_id, body.reason, hours=24)
        elif body.duration == "session":
            database.add_song_suppression(song_id, body.reason, session_id=session_id)
        elif body.duration == "permanent":
            database.blacklist_song(song_id, level="never_play", reason=body.reason or "user_skip")

    # Record audio embedding for mood-specific repulsion
    if config.MUQ_ENABLED and qm._mood.get_state() != "neutral":
        if song_id:
            try:
                from llm.muq_engine import is_available
                if is_available():
                    song = database.get_song(song_id)
                    if song and song.get("filepath"):
                        _, audio_emb = _fetch_song_embeddings(song_id)
                        if audio_emb:
                            database.record_mood_skip_repulsion(
                                qm._mood.get_state(), body.reason,
                                song_id, audio_emb,
                            )
            except Exception:
                pass  # Best effort — don't fail the feedback submission

    return {"status": "recorded", "reason": body.reason, "duration": body.duration}


# ── Undo Actions ────────────────────────────────────────────
@app.post("/api/queue/undo-skip")
def undo_skip():
    """Undo the last skip: replay the skipped song."""
    qm = _get_queue_manager()
    result = qm.undo_skip()
    if result.get("success"):
        broadcast("song_undo", {"action": "skip", "song_id": result["song_id"]})
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
    return result


@app.post("/api/queue/undo-thumbs-down")
def undo_thumbs_down():
    """Undo the last thumbs-down."""
    qm = _get_queue_manager()
    result = qm.undo_thumbs_down()
    if result.get("success"):
        broadcast("song_undo", {"action": "thumbs_down", "song_id": result["song_id"]})
    return result


@app.get("/api/queue/undo-state")
def get_undo_state():
    """Get the current undo state for the frontend."""
    qm = _get_queue_manager()
    return qm.get_undo_state()


# ── Sleep Timer ─────────────────────────────────────────────
@app.post("/api/sleep/timer")
def set_sleep_timer(body: SleepTimerRequest):
    """Start the sleep timer."""
    st = _get_sleep_timer()
    st.start(body.minutes, body.fade_seconds, body.switch_to_chill)
    return {"status": "started", "minutes": body.minutes, "fade_seconds": body.fade_seconds}


@app.delete("/api/sleep/timer")
def cancel_sleep_timer():
    """Cancel the sleep timer."""
    st = _get_sleep_timer()
    st.cancel()
    return {"status": "cancelled"}


@app.get("/api/sleep/timer")
def get_sleep_timer():
    """Get sleep timer status."""
    st = _get_sleep_timer()
    return st.get_status()


# ============================================================
# Mood Endpoints
# ============================================================

@app.get("/api/mood")
def get_mood():
    msm = _get_mood_state_machine()
    vibe_lock = msm.get_vibe_lock_info()
    return {
        "state": msm.get_state(),
        "vibe_lock_active": vibe_lock.get("active", False),
        "vibe_lock_songs_remaining": vibe_lock.get("songs_remaining", 0),
    }


@app.post("/api/mood/set")
def set_mood(req: MoodSetRequest):
    msm = _get_mood_state_machine()
    success = msm.set_state(req.state)
    if success:
        database.record_mood_change(_current_session_id, req.state, source="button")
        return {"status": "mood_set", "state": req.state}
    return {"error": f"Unknown mood state: {req.state}"}


@app.post("/api/mood/vibe-lock/activate")
def activate_vibe_lock(req: VibeLockActivateRequest):
    """Activate vibe lock for a specific song.

    Stores both text and audio embeddings for the locked song.
    The audio embedding is used for Musically-similar recommendations.
    """
    msm = _get_mood_state_machine()
    song = database.get_song(req.song_id)
    if not song:
        return {"error": "Song not found"}

    text_embedding, audio_embedding = _fetch_song_embeddings(req.song_id)

    if text_embedding:
        msm.activate_vibe_lock(req.song_id, text_embedding, audio_embedding=audio_embedding)
        return {
            "status": "vibe_lock_active",
            "song_id": req.song_id,
            "expires_in_minutes": 30,
        }
    return {"error": "Could not get song embedding"}


@app.post("/api/mood/vibe-lock/deactivate")
def deactivate_vibe_lock():
    """Deactivate vibe lock."""
    msm = _get_mood_state_machine()
    msm.deactivate_vibe_lock()
    return {"status": "vibe_lock_deactivated"}


# ============================================================
# Session History + Stats + Similarity Endpoints
# ============================================================

@app.get("/api/sessions")
def list_sessions(limit: int = 20):
    """List past sessions with summary info."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT s.id, s.started_at, s.ended_at, s.initial_mood, s.mood_changes,
                      s.session_type,
                      COUNT(pe.id) as song_count,
                      SUM(CASE WHEN pe.signal = 'completed' THEN 1 ELSE 0 END) as completed_count,
                      SUM(CASE WHEN pe.signal IN ('skip_early', 'skip_late') THEN 1 ELSE 0 END) as skip_count
               FROM sessions s
               LEFT JOIN play_events pe ON pe.session_id = s.id
               WHERE s.ended_at IS NOT NULL
               GROUP BY s.id
               ORDER BY s.started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    """Get session detail with play events and mood changes."""
    conn = database.get_connection()
    try:
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            return {"error": "Session not found"}
        session = dict(session)
        # Parse mood_changes JSON
        if session.get("mood_changes"):
            import json as _json
            session["mood_changes"] = _json.loads(session["mood_changes"])
        # Get play events for this session
        events = conn.execute(
            """SELECT pe.*, s.artist, s.title FROM play_events pe
               LEFT JOIN songs s ON pe.song_id = s.id
               WHERE pe.session_id = ?
               ORDER BY pe.played_at""",
            (session_id,),
        ).fetchall()
        session["events"] = [dict(e) for e in events]
        # Compute duration
        if session.get("started_at") and session.get("ended_at"):
            from datetime import datetime as _dt
            start = _dt.fromisoformat(session["started_at"])
            end = _dt.fromisoformat(session["ended_at"])
            session["duration_secs"] = (end - start).total_seconds()
        return session
    finally:
        conn.close()


@app.get("/api/stats/overview")
def stats_overview():
    """Aggregated listening statistics."""
    conn = database.get_connection()
    try:
        total_plays = conn.execute("SELECT COUNT(*) as cnt FROM play_events").fetchone()["cnt"]
        completed = conn.execute("SELECT COUNT(*) as cnt FROM play_events WHERE signal = 'completed'").fetchone()["cnt"]
        skipped = conn.execute("SELECT COUNT(*) as cnt FROM play_events WHERE signal IN ('skip_early', 'skip_late')").fetchone()["cnt"]
        hearted = conn.execute("SELECT COUNT(*) as cnt FROM play_events WHERE signal = 'hearted'").fetchone()["cnt"]
        replayed = conn.execute("SELECT COUNT(*) as cnt FROM play_events WHERE signal = 'replayed'").fetchone()["cnt"]
        unique_songs = conn.execute("SELECT COUNT(DISTINCT song_id) as cnt FROM play_events").fetchone()["cnt"]
        total_songs = conn.execute("SELECT COUNT(*) as cnt FROM songs").fetchone()["cnt"]

        # Total listening time (prorated by completion_pct)
        listen_row = conn.execute(
            """SELECT COALESCE(SUM(s.duration_secs * COALESCE(pe.completion_pct, 100) / 100.0), 0) as secs FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE pe.signal IN ('completed', 'skip_early', 'skip_late')"""
        ).fetchone()
        total_listen_secs = listen_row["secs"]

        # Session stats
        session_count = conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()["cnt"]
        songs_per_session = round(total_plays / max(1, session_count), 1)

        # Top artists
        top_artists = conn.execute(
            """SELECT s.artist, COUNT(*) as cnt FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE s.artist IS NOT NULL AND pe.signal != 'playing'
               GROUP BY s.artist ORDER BY cnt DESC LIMIT 5"""
        ).fetchall()

        # Top songs
        top_songs = conn.execute(
            """SELECT s.artist, s.title, COUNT(*) as cnt FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE pe.signal != 'playing'
               GROUP BY pe.song_id ORDER BY cnt DESC LIMIT 5"""
        ).fetchall()

        # Listening by hour
        by_hour = conn.execute(
            """SELECT hour_of_day, COUNT(*) as cnt FROM play_events
               WHERE hour_of_day IS NOT NULL
               GROUP BY hour_of_day ORDER BY hour_of_day"""
        ).fetchall()

        # Mood distribution
        by_mood = conn.execute(
            """SELECT mood_state, COUNT(*) as cnt FROM play_events
               WHERE mood_state IS NOT NULL
               GROUP BY mood_state ORDER BY cnt DESC"""
        ).fetchall()

        # Average completion rate
        avg_comp = conn.execute(
            "SELECT AVG(avg_completion) as avg FROM songs WHERE avg_completion > 0"
        ).fetchone()["avg"] or 0

        # Most completed song
        most_completed = conn.execute(
            "SELECT title, artist, avg_completion FROM songs WHERE avg_completion > 0 ORDER BY avg_completion DESC LIMIT 1"
        ).fetchone()

        # Weekly trend (last 7 days)
        weekly = conn.execute(
            """SELECT DATE(played_at) as day, COUNT(*) as cnt FROM play_events
               WHERE played_at >= DATE('now', '-6 days')
               GROUP BY day ORDER BY day"""
        ).fetchall()
        # Fill in missing days
        from datetime import date as _date, timedelta
        today = _date.today()
        day_map = {r["day"]: r["cnt"] for r in weekly}
        weekly_plays = []
        for i in range(6, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            weekly_plays.append({"date": d, "count": day_map.get(d, 0)})

        # Energy distribution
        energy_buckets = conn.execute(
            """SELECT CASE
                 WHEN energy >= 0.66 THEN 'high'
                 WHEN energy >= 0.33 THEN 'mid'
                 ELSE 'low' END as bucket,
               COUNT(*) as cnt FROM songs WHERE play_count > 0
               GROUP BY bucket"""
        ).fetchall()
        bucket_map = {"low": 0, "mid": 0, "high": 0}
        for b in energy_buckets:
            bucket_map[b["bucket"]] = b["cnt"]
        total_buckets = max(sum(bucket_map.values()), 1)

        # Cluster distribution
        cluster_dist = conn.execute(
            """SELECT s.cluster_id, COUNT(*) as cnt FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE s.cluster_id >= 0
               GROUP BY s.cluster_id ORDER BY cnt DESC LIMIT 8"""
        ).fetchall()

        # Top genres from songs with most plays
        genres_raw = conn.execute(
            """SELECT s.genre_tags, COUNT(*) as play_cnt FROM play_events pe
               JOIN songs s ON pe.song_id = s.id
               WHERE s.genre_tags IS NOT NULL AND pe.signal != 'playing'
               GROUP BY pe.song_id ORDER BY play_cnt DESC LIMIT 20"""
        ).fetchall()
        genre_counter = {}
        for g in genres_raw:
            try:
                tags = json.loads(g["genre_tags"])
                for t in tags:
                    genre_counter[t] = genre_counter.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_genres = sorted(genre_counter.items(), key=lambda x: -x[1])[:6]
        total_genre_plays = max(sum(genre_counter.values()), 1)

        return {
            "total_plays": total_plays,
            "completed": completed,
            "skipped": skipped,
            "hearted": hearted,
            "replayed": replayed,
            "unique_songs_played": unique_songs,
            "total_songs_in_library": total_songs,
            "total_listen_secs": round(total_listen_secs),
            "completion_rate": round(completed / max(1, total_plays), 3),
            "skip_rate": round(skipped / max(1, total_plays), 3),
            "avg_completion_rate": round(avg_comp, 1),
            "session_count": session_count,
            "avg_songs_per_session": songs_per_session,
            "top_artists": [{"artist": r["artist"], "plays": r["cnt"]} for r in top_artists],
            "top_songs": [{"artist": r["artist"], "title": r["title"], "plays": r["cnt"]} for r in top_songs],
            "by_hour": [{"hour": r["hour_of_day"], "count": r["cnt"]} for r in by_hour],
            "by_mood": [{"mood": r["mood_state"], "count": r["cnt"]} for r in by_mood],
            "weekly_plays": weekly_plays,
            "energy_distribution": [
                {"bucket": "low", "count": bucket_map["low"], "pct": round(bucket_map["low"] / total_buckets * 100)},
                {"bucket": "mid", "count": bucket_map["mid"], "pct": round(bucket_map["mid"] / total_buckets * 100)},
                {"bucket": "high", "count": bucket_map["high"], "pct": round(bucket_map["high"] / total_buckets * 100)},
            ],
            "cluster_distribution": [
                {"cluster_id": r["cluster_id"], "count": r["cnt"]} for r in cluster_dist
            ],
            "top_genres": [{"genre": g, "pct": round(c / total_genre_plays * 100)} for g, c in top_genres],
            "most_completed": {
                "title": most_completed["title"] if most_completed else None,
                "artist": most_completed["artist"] if most_completed else None,
                "avg_completion": round(most_completed["avg_completion"], 1) if most_completed else 0,
            },
        }
    finally:
        conn.close()


@app.get("/api/stats/daily-report")
def daily_report():
    """Today's listening summary."""
    from datetime import date as _date
    today = _date.today().isoformat()
    conn = database.get_connection()
    try:
        events = conn.execute(
            """SELECT pe.*, s.artist, s.title, s.duration_secs, s.energy FROM play_events pe
               LEFT JOIN songs s ON pe.song_id = s.id
               WHERE DATE(pe.played_at) = ?
               ORDER BY pe.played_at""",
            (today,),
        ).fetchall()
        if not events:
            return {"date": today, "total_songs": 0, "message": "No listening today yet."}

        total = len(events)
        completed = sum(1 for e in events if e["signal"] == "completed")
        skipped = sum(1 for e in events if e["signal"] in ("skip_early", "skip_late"))
        hearted = sum(1 for e in events if e["signal"] == "hearted")
        replayed = sum(1 for e in events if e["signal"] == "replayed")

        # Listening time (prorated by completion_pct)
        listen_secs = sum(
            (e["duration_secs"] or 0) * (e["completion_pct"] or 0) / 100.0
            for e in events if e["signal"] in ("completed", "skip_early", "skip_late")
        )

        # Unique songs
        unique = len(set(e["song_id"] for e in events if e["song_id"]))

        # Top artists today
        artist_counts = {}
        for e in events:
            a = e["artist"]
            if a:
                artist_counts[a] = artist_counts.get(a, 0) + 1
        top_artists = sorted(artist_counts.items(), key=lambda x: -x[1])[:5]

        # Top songs today
        song_counts = {}
        for e in events:
            sid = e["song_id"]
            if sid and e["artist"] and e["title"]:
                key = (e["artist"], e["title"])
                song_counts[key] = song_counts.get(key, 0) + 1
        top_songs = sorted(song_counts.items(), key=lambda x: -x[1])[:5]

        # Mood distribution
        mood_counts = {}
        for e in events:
            m = e["mood_state"]
            if m:
                mood_counts[m] = mood_counts.get(m, 0) + 1

        # Avg energy today
        energies = [e["energy"] for e in events if e["energy"] is not None]
        energy_avg = round(sum(energies) / max(len(energies), 1), 2)

        # Session count today
        session_ids = set(e["session_id"] for e in events if e["session_id"])
        session_count = len(session_ids)

        return {
            "date": today,
            "total_songs": total,
            "unique_songs": unique,
            "completed": completed,
            "skipped": skipped,
            "hearted": hearted,
            "replayed": replayed,
            "listen_minutes": round(listen_secs / 60, 1),
            "completion_rate": round(completed / max(1, total), 3),
            "energy_avg": energy_avg,
            "session_count": session_count,
            "top_artists": [{"artist": a, "plays": c} for a, c in top_artists],
            "top_songs": [{"artist": a, "title": t, "plays": c} for (a, t), c in top_songs],
            "mood_distribution": mood_counts,
        }
    finally:
        conn.close()


@app.get("/api/energy/trajectory")
def energy_trajectory():
    """Get current energy trajectory state."""
    from engine.energy import get_trajectory
    traj = get_trajectory()
    return {
        "direction": traj.get_direction(),
        "current_energy": traj.get_current_energy(),
        "recent_energies": [e["energy"] for e in traj._recent_energies],
    }


@app.get("/api/mood/history")
def mood_history():
    """Get mood change history for the current session."""
    if not _current_session_id:
        return {"mood_changes": []}
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT mood_changes FROM sessions WHERE id = ?", (_current_session_id,)).fetchone()
        if row and row["mood_changes"]:
            import json as _json
            return {"mood_changes": _json.loads(row["mood_changes"])}
        return {"mood_changes": []}
    finally:
        conn.close()


@app.get("/api/mood/centroid")
def get_mood_centroid(mood: str):
    """Get the audio embedding centroid for a mood (computed from completed plays).

    Returns the centroid vector and availability status.
    The centroid is the average MuQ-MuLan audio embedding of songs completed
    in the given mood, representing the user's personal "sound" for that mood.
    """
    msm = _get_mood_state_machine()
    centroid = msm.get_mood_centroid(mood)
    if centroid is None:
        return {"centroid": None, "available": False}
    return {"centroid": centroid, "available": True}


@app.get("/api/songs/{song_id:int}/similar")
def similar_songs(song_id: int, limit: int = 10, source: str = "auto"):
    """Find songs similar to a given song using ChromaDB embedding search.

    source param:
      - "auto" (default): prefer audio embeddings if available, fall back to text
      - "audio": force MuQ-MuLan audio embeddings (musical similarity)
      - "text": force text embeddings (metadata/description similarity)
    """
    from engine.retriever import get_chroma_collection

    # Determine which collection to use
    use_audio = False
    if source in ("auto", "audio") and config.MUQ_ENABLED:
        try:
            audio_collection = get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
            audio_check = audio_collection.get(ids=[str(song_id)], include=["embeddings"])
            if audio_check.get("embeddings") is not None and len(audio_check["embeddings"]) > 0 and audio_check["embeddings"][0] is not None:
                use_audio = True
        except Exception:
            pass

    if use_audio:
        collection = get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
    else:
        collection = get_chroma_collection("songs")

    try:
        results = collection.get(
            ids=[str(song_id)],
            include=["embeddings"],
        )
        if results.get("embeddings") is None or len(results["embeddings"]) == 0 or results["embeddings"][0] is None:
            # Fallback to the other collection
            fallback = get_chroma_collection("songs") if use_audio else None
            if fallback:
                results = fallback.get(ids=[str(song_id)], include=["embeddings"])
                collection = fallback
        if results.get("embeddings") is None or len(results["embeddings"]) == 0 or results["embeddings"][0] is None:
                return {"error": "Song has no embedding"}
        embedding = results["embeddings"][0]

        similar = collection.query(
            query_embeddings=[embedding],
            n_results=limit + 1,
            include=["distances", "metadatas"],
        )

        songs = []
        for i in range(len(similar["ids"][0])):
            sid = int(similar["ids"][0][i])
            if sid == song_id:
                continue
            meta = similar["metadatas"][0][i]
            dist = similar["distances"][0][i]
            score = 1.0 - dist
            song = database.get_song(sid)
            if song:
                songs.append({
                    "song_id": sid,
                    "score": round(score, 4),
                    "artist": song.get("artist", ""),
                    "title": song.get("title", ""),
                    "energy": float(meta.get("energy", 0.5)),
                    "valence": float(meta.get("valence", 0.5)),
                    "source": "audio" if use_audio else "text",
                })
            if len(songs) >= limit:
                break
        return songs
    except Exception as e:
        return {"error": str(e)}


class MusicSearchRequest(BaseModel):
    text: str
    limit: int = 10


@app.post("/api/search/music")
def search_music(req: MusicSearchRequest):
    """Search for songs by text description using MuQ-MuLan cross-modal embeddings.

    Embeds the text query with MuQ-MuLan's text encoder, then queries
    the audio embedding collection for musically similar songs.
    """
    if not config.MUQ_ENABLED:
        return {"error": "MuQ is disabled (set MUQ_ENABLED=true)"}

    try:
        from llm.muq_engine import is_available, embed_texts
        if not is_available():
            return {"error": "MuQ-MuLan not available (install torch + muq)"}
    except Exception as e:
        return {"error": f"MuQ check failed: {e}"}

    try:
        # Embed the text query
        text_embeds = embed_texts([req.text])
        if not text_embeds:
            return {"error": "Failed to embed text query"}
        query_vector = text_embeds[0]

        # Query audio collection
        from engine.retriever import get_chroma_collection
        audio_collection = get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
        results = audio_collection.query(
            query_embeddings=[query_vector],
            n_results=req.limit,
            include=["distances", "metadatas"],
        )

        songs = []
        for i in range(len(results["ids"][0])):
            sid = int(results["ids"][0][i])
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i]
            score = 1.0 - dist
            song = database.get_song(sid)
            if song:
                songs.append({
                    "song_id": sid,
                    "score": round(score, 4),
                    "artist": song.get("artist", ""),
                    "title": song.get("title", ""),
                    "energy": float(meta.get("energy", 0.5)),
                    "valence": float(meta.get("valence", 0.5)),
                })
        return songs
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Folder Filter Endpoints
# ============================================================

class FolderFilterRequest(BaseModel):
    folders: list[str]


@app.get("/api/songs/subfolders")
def list_subfolders():
    """List all unique subfolders with song counts."""
    import math
    subfolders = database.get_subfolders()
    counts = database.get_subfolder_song_counts()
    total = database.get_total_song_count()
    return {
        "subfolders": [{"name": f, "count": counts.get(f, 0)} for f in subfolders],
        "total_songs": total,
    }


@app.get("/api/filter/folder")
def get_folder_filter():
    """Get the current folder filter setting (JSON array of folder names)."""
    val = database.get_setting("folder_filter") or ""
    folders = json.loads(val) if val else []
    return {"folders": folders}


@app.post("/api/filter/folder")
def set_folder_filter(req: FolderFilterRequest):
    """Set folder filter to restrict recommendations to specific folders.

    Body: {"folders": ["song 40", "song 41", "song 42"]}
    """
    database.set_setting("folder_filter", json.dumps(req.folders))
    # Trigger queue rebuild unconditionally
    qm = _get_queue_manager()
    p = player_engine.get_player()
    current_song_id = p.get_current_song_id()
    session_id = _current_session_id
    qm.on_song_started(current_song_id, session_id)
    broadcast("folder_filter_changed", {"folders": req.folders})
    return {"status": "set", "folders": req.folders}


@app.post("/api/filter/folder/clear")
def clear_folder_filter():
    """Clear the folder filter and rebuild queue."""
    database.set_setting("folder_filter", "")
    # Trigger queue rebuild unconditionally
    qm = _get_queue_manager()
    p = player_engine.get_player()
    current_song_id = p.get_current_song_id()
    session_id = _current_session_id
    qm.on_song_started(current_song_id, session_id)
    broadcast("folder_filter_changed", {"folders": []})
    return {"status": "cleared"}


# ============================================================
# Settings Endpoints
# ============================================================

@app.get("/api/settings")
def get_settings():
    return database.get_all_settings()


@app.put("/api/settings/{key}")
def update_setting(key: str, body: dict):
    value = body.get("value", "")
    database.set_setting(key, value)
    # Hot-reload provider settings
    if key in ("chat_provider", "embed_provider"):
        try:
            from llm import reset_providers
            reset_providers()
        except Exception as e:
            print(f"[llm] Provider reset failed: {e}")
    return {"status": "updated", "key": key, "value": value}


@app.post("/api/settings/reset-recommendations")
def reset_recommendations():
    """Reset all recommendation data: play events, transitions, song stats, and settings."""
    from engine import energy as energy_engine
    qm = _get_queue_manager()
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM play_events")
        conn.execute("DELETE FROM transitions")
        conn.execute("""UPDATE songs SET
            play_count = 0, skip_count = 0, complete_count = 0,
            replay_count = 0, avg_completion = 0.0, recovery_score = 0.5,
            last_played = NULL""")
        conn.commit()
    finally:
        conn.close()

    database.set_setting("exploration_level", "0.3")
    database.set_setting("genre_filter", "")
    database.set_setting("artist_filter", "")
    database.set_setting("vibe_lock_active", "false")
    database.set_setting("vibe_lock_song_id", "NULL")
    database.set_setting("vibe_lock_expires", "NULL")
    database.set_setting("onboarding_complete", "false")

    qm._recently_recommended_ids = set()
    qm._skip_streak = 0
    qm.clear_queue()
    traj = energy_engine.get_trajectory()
    traj._recent_energies = []
    _get_mood_state_machine().deactivate_vibe_lock()

    logger.info("Recommendations reset by user")
    return {"status": "reset", "message": "Recommendations reset. Restart to re-seed your taste."}


@app.get("/api/settings/export")
def export_settings():
    """Export all settings, collections, and custom song metadata as JSON."""
    settings = database.get_all_settings()
    collections = database.get_collections_with_songs()
    metadata = database.get_user_modified_songs()

    return {
        "exported_at": datetime.now().isoformat(),
        "version": "2.4.1",
        "settings": settings,
        "collections": [
            {
                "name": c["name"],
                "description": c.get("description", ""),
                "song_ids": c.get("song_ids", []),
            }
            for c in collections
        ],
        "song_metadata": {
            str(s["id"]): {
                "user_tags": s.get("user_tags"),
                "user_notes": s.get("user_notes"),
                "dj_effect_eligible": s.get("dj_effect_eligible"),
            }
            for s in metadata
        },
    }


@app.post("/api/settings/import")
def import_settings(body: dict):
    """Import settings from JSON export. Merges with existing data."""
    data = body.get("data", body)
    imported_settings = 0
    imported_collections = 0
    imported_metadata = 0

    if "settings" in data:
        for key, value in data["settings"].items():
            database.set_setting(key, str(value))
            imported_settings += 1

    if "collections" in data:
        for col in data["collections"]:
            existing = database.get_collection_by_name(col.get("name", ""))
            if existing:
                continue
            col_id = database.create_collection(
                col.get("name", "Imported"),
                col.get("description", ""),
            )
            for i, song_id in enumerate(col.get("song_ids", [])):
                database.add_to_collection(col_id, song_id, position=i)
            imported_collections += 1

    if "song_metadata" in data:
        for song_id_str, meta in data["song_metadata"].items():
            try:
                song_id = int(song_id_str)
            except (ValueError, TypeError):
                continue
            updates = {}
            if meta.get("user_tags") is not None:
                updates["user_tags"] = meta["user_tags"]
            if meta.get("user_notes") is not None:
                updates["user_notes"] = meta["user_notes"]
            if meta.get("dj_effect_eligible") is not None:
                updates["dj_effect_eligible"] = int(meta["dj_effect_eligible"])
            if updates:
                database.update_song(song_id, **updates)
                imported_metadata += 1

    logger.info("Settings import: %d settings, %d collections, %d song metadata", imported_settings, imported_collections, imported_metadata)
    return {
        "status": "imported",
        "settings_count": imported_settings,
        "collections_count": imported_collections,
        "song_metadata_count": imported_metadata,
    }


@app.post("/api/settings/wipe-db")
def wipe_database():
    """Wipe all database data, embeddings, and cached files. Requires restart."""
    import shutil
    import config
    import os

    try:
        # Close any existing DB connection
        try:
            conn = database.get_connection()
            conn.close()
        except Exception:
            pass

        # Remove DB file
        if os.path.exists(config.DB_PATH):
            os.remove(config.DB_PATH)
            logger.info(f"Removed DB: {config.DB_PATH}")

        # Remove ChromaDB data
        if os.path.exists(config.CHROMA_PATH):
            shutil.rmtree(config.CHROMA_PATH)
            logger.info(f"Removed ChromaDB: {config.CHROMA_PATH}")

        # Remove art files
        if os.path.exists(config.ART_DIR):
            shutil.rmtree(config.ART_DIR)
            logger.info(f"Removed art dir: {config.ART_DIR}")

        # Remove TTS cache
        tts_cache = config.TTS_CACHE_DIR
        if os.path.exists(tts_cache):
            shutil.rmtree(tts_cache)
            logger.info(f"Removed TTS cache: {tts_cache}")

        logger.info("Full database wipe complete")
        return {"status": "wiped", "message": "Database wiped. Please restart the server."}
    except Exception as e:
        logger.error(f"Database wipe failed: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/taste/reseed")
def reseed_taste():
    """Reseed taste: clear immediate + session memory layers, keep long-term stats."""
    qm = _get_queue_manager()

    # Delete last 50 positive signals (immediate + session layers)
    conn = database.get_connection()
    try:
        conn.execute("""
            DELETE FROM play_events
            WHERE id IN (
                SELECT id FROM play_events
                WHERE signal IN ('hearted', 'replayed', 'completed', 'onboard_heart')
                ORDER BY played_at DESC
                LIMIT 50
            )
        """)
        conn.commit()
    finally:
        conn.close()

    # Reset queue state
    qm._recently_recommended_ids = set()
    qm._skip_streak = 0
    qm.clear_queue()
    _get_mood_state_machine().deactivate_vibe_lock()

    # Invalidate constellations (will rebuild on next queue)
    if hasattr(qm, '_constellations') and qm._constellations:
        qm._constellations.invalidate()

    # Trigger onboarding pick screen
    database.set_setting("onboarding_complete", "false")

    broadcast("taste_reseeded", {})
    return {"status": "reseeded", "message": "Taste reseeded. Pick 5 songs to re-calibrate."}


@app.get("/api/taste/constellations")
def get_constellations():
    """Return current taste constellation data."""
    qm = _get_queue_manager()
    if not hasattr(qm, '_constellations') or not qm._constellations:
        return {"active": False, "reason": "not_initialized"}
    return qm._constellations.get_constellation_summary()


# ============================================================
# LLM Status
# ============================================================

@app.get("/api/llm/status")
def llm_status():
    """Return health status of all configured LLM providers."""
    result = {
        "chat": {"openai": None, "ollama": None},
        "embed": {"openai": None, "ollama": None},
        "active_chat": None,
        "active_embed": None,
    }

    # Determine which providers to check
    chat_provider = "auto"
    embed_provider = "auto"
    try:
        from db import database
        cp = database.get_setting("chat_provider")
        if cp:
            chat_provider = cp
        ep = database.get_setting("embed_provider")
        if ep:
            embed_provider = ep
    except Exception:
        pass
    if chat_provider not in ("openai", "ollama", "auto"):
        chat_provider = config.CHAT_PROVIDER or "auto"
    if embed_provider not in ("openai", "ollama", "auto"):
        embed_provider = config.EMBED_PROVIDER or "auto"

    def _check_chat_openai():
        try:
            from llm.openai_provider import OpenAIChatClient
            client = OpenAIChatClient()
            client.chat(None, [{"role": "user", "content": "hi"}], timeout=5)
            return {"healthy": True, "model": config.OPENAI_CHAT_MODEL}
        except Exception as e:
            return {"healthy": False, "error": str(e)[:200]}

    def _check_chat_ollama():
        try:
            from llm.ollama_provider import OllamaChatClient
            client = OllamaChatClient()
            client.chat(None, [{"role": "user", "content": "hi"}], timeout=5)
            return {"healthy": True, "model": config.OLLAMA_CHAT_MODEL}
        except Exception as e:
            return {"healthy": False, "error": str(e)[:200]}

    def _check_embed_openai():
        try:
            from llm.openai_provider import OpenAIEmbedClient
            client = OpenAIEmbedClient()
            client.embed(None, "test", timeout=5)
            return {"healthy": True, "model": config.OPENAI_EMBED_MODEL}
        except Exception as e:
            return {"healthy": False, "error": str(e)[:200]}

    def _check_embed_ollama():
        try:
            from llm.ollama_provider import OllamaEmbedClient
            client = OllamaEmbedClient()
            client.embed(None, "test", timeout=5)
            return {"healthy": True, "model": config.OLLAMA_EMBED_MODEL}
        except Exception as e:
            return {"healthy": False, "error": str(e)[:200]}

    # Chat providers
    if chat_provider in ("openai", "auto"):
        result["chat"]["openai"] = _check_chat_openai()
    if chat_provider in ("ollama", "auto"):
        result["chat"]["ollama"] = _check_chat_ollama()

    # Embed providers
    if embed_provider in ("openai", "auto"):
        result["embed"]["openai"] = _check_embed_openai()
    if embed_provider in ("ollama", "auto"):
        result["embed"]["ollama"] = _check_embed_ollama()

    # Determine active provider (first healthy one)
    if chat_provider == "openai" and result["chat"]["openai"]:
        if result["chat"]["openai"].get("healthy"):
            result["active_chat"] = "openai"
    elif chat_provider == "ollama" and result["chat"]["ollama"]:
        if result["chat"]["ollama"].get("healthy"):
            result["active_chat"] = "ollama"
    elif chat_provider == "auto":
        if result["chat"]["openai"] and result["chat"]["openai"].get("healthy"):
            result["active_chat"] = "openai"
        elif result["chat"]["ollama"] and result["chat"]["ollama"].get("healthy"):
            result["active_chat"] = "ollama"

    if embed_provider == "openai" and result["embed"]["openai"]:
        if result["embed"]["openai"].get("healthy"):
            result["active_embed"] = "openai"
    elif embed_provider == "ollama" and result["embed"]["ollama"]:
        if result["embed"]["ollama"].get("healthy"):
            result["active_embed"] = "ollama"
    elif embed_provider == "auto":
        if result["embed"]["openai"] and result["embed"]["openai"].get("healthy"):
            result["active_embed"] = "openai"
        elif result["embed"]["ollama"] and result["embed"]["ollama"].get("healthy"):
            result["active_embed"] = "ollama"

    return result


# ============================================================
# Mode Toggle
# ============================================================

class ModeToggleRequest(BaseModel):
    mode: str


@app.post("/api/mode/toggle")
def toggle_mode(req: ModeToggleRequest):
    """Switch between radio and normal mode."""
    if req.mode not in ("radio", "normal"):
        return {"error": "Invalid mode. Must be 'radio' or 'normal'"}
    database.set_setting("current_mode", req.mode)
    # Clear queue when switching modes
    qm = _get_queue_manager()
    with qm._condition:
        qm._queue.clear()
    return {"status": "toggled", "mode": req.mode}


# ============================================================
# Recommendation Endpoint
# ============================================================

@app.get("/api/recommend")
def get_recommendation():
    """Get a single song recommendation using the full recommendation engine."""
    qm = _get_queue_manager()
    pick = qm.pop_next()
    if pick:
        if pick.get("type") == "talk_segment":
            # Return the next actual song instead
            with qm._condition:
                for item in qm._queue:
                    if item.get("type") != "talk_segment":
                        return {
                            "song_id": item["song_id"],
                            "slot": item.get("slot", "context"),
                            "reason": item.get("reason", ""),
                        }
            return {"info": "talk_segment", "segment_type": pick.get("segment_type")}
        return {
            "song_id": pick["song_id"],
            "slot": pick.get("slot", "context"),
            "reason": pick.get("reason", ""),
        }
    return {"error": "No recommendation available"}


# ============================================================
# Archetype Endpoint
# ============================================================

@app.get("/api/archetype")
def get_archetype():
    """Return detected listener archetype."""
    archetype_name, metrics = archetype_engine.detect_archetype()
    return {
        "archetype": archetype_name,
        "description": {
            "explorer": "Loves discovering new music",
            "comfort_listener": "Prefers familiar favorites",
            "deep_listener": "Listens to songs all the way through",
            "mood_listener": "Chooses music based on mood",
        }.get(archetype_name, "Music enthusiast"),
        "metrics": metrics,
    }


# ============================================================
# Announcement Endpoint
# ============================================================

_async_announcement_text = None
_async_announcement_time = 0


def _generate_announcement_background():
    """Generate announcement in background thread."""
    global _async_announcement_text, _async_announcement_time
    try:
        from announcer import get_announcement, AnnounceTrigger
        p = player_engine.get_player()
        qm = _get_queue_manager()
        msm = _get_mood_state_machine()

        current_song_id = p.get_current_song_id()
        current_song = database.get_song(current_song_id) if current_song_id else None
        next_song = None
        next_song_id = None
        with qm._condition:
            if qm._queue:
                next_song_id = qm._queue[0].get("song_id")
        if next_song_id:
            next_song = database.get_song(next_song_id)

        context = {
            "current_song": dict(current_song) if current_song else {},
            "next_song": dict(next_song) if next_song else {},
            "mood_state": msm.get_state(),
            "skip_streak": qm._skip_streak,
            "hour": datetime.now().hour,
        }

        text = get_announcement(context, AnnounceTrigger.RADIO_INTRO)
        if text:
            _async_announcement_text = text
            _async_announcement_time = time.time()
    except Exception as e:
        print(f"Background announcement failed: {e}")


@app.get("/api/announcement/next")
def get_next_announcement():
    """Return next scheduled announcement text (quick fallback + background async generation)."""
    global _async_announcement_text, _async_announcement_time

    # Return cached async announcement if available and recent (< 30s)
    if _async_announcement_text and (time.time() - _async_announcement_time) < 30:
        return {"text": _async_announcement_text}

    # Quick fallback: generate a simple announcement immediately
    p = player_engine.get_player()
    qm = _get_queue_manager()
    current_song_id = p.get_current_song_id()
    current_song = database.get_song(current_song_id) if current_song_id else None

    next_song = None
    next_song_id = None
    with qm._condition:
        if qm._queue:
            next_song_id = qm._queue[0].get("song_id")
    if next_song_id:
        next_song = database.get_song(next_song_id)

    artist = next_song.get("artist", "the artist") if next_song else "the artist"
    title = next_song.get("title", "a track") if next_song else "a track"
    fallback = f"Next up, {artist} - {title}."

    # Start background generation of a better announcement
    threading.Thread(target=_generate_announcement_background, daemon=True).start()

    return {"text": fallback}


# ============================================================
# TTS Status
# ============================================================

@app.get("/api/tts/status")
def tts_status():
    """Return TTS and voice engine status."""
    qm = _get_queue_manager()
    p = player_engine.get_player()
    next_song_id = None
    next_is_talk = False
    with qm._condition:
        next_is_talk = len(qm._pending_talk_segments) > 0
        if qm._queue:
            first = qm._queue[0]
            next_song_id = first.get("song_id")

    ready = False
    if next_song_id:
        with qm._pregen_lock:
            cached = qm._pregen_cache.get(next_song_id)
            ready = bool(cached and cached.get("wav_path") and not cached.get("no_announcement"))

    # Voice manager status
    try:
        from announcer.voice_manager import get_voice_manager
        vm_status = get_voice_manager().get_status()
    except Exception:
        vm_status = {"active_engine": "piper", "active_voice": ""}

    return {
        "next_song_ready": ready,
        "next_is_talk_segment": next_is_talk,
        "pregen_cache_size": len(qm._pregen_cache),
        "tts_enabled": config.TTS_ENABLED,
        "talk_segments_enabled": (database.get_setting("talk_segments_enabled") or "false").lower() == "true",
        "voice_engine": vm_status.get("active_engine"),
        "active_voice": vm_status.get("active_voice"),
        "voice_mode": vm_status.get("voice_mode", "user_select"),
        "gpu_available": vm_status.get("gpu_available"),
        "omnivoice_ready": vm_status.get("omnivoice_ready", False),
        "all_voices": vm_status.get("all_voices", {}),
    }


# ============================================================
# Radio Talk Show Endpoints
# ============================================================

@app.get("/api/talk/config")
def get_talk_config():
    """Get all talk show settings."""
    return {
        "talk_segments_enabled": database.get_setting("talk_segments_enabled") or "false",
        "talk_caller_enabled": database.get_setting("talk_caller_enabled") or "true",
        "talk_caller_mode": database.get_setting("talk_caller_mode") or "influences_queue",
        "talk_monologue_enabled": database.get_setting("talk_monologue_enabled") or "true",
        "talk_guest_takeover_enabled": database.get_setting("talk_guest_takeover_enabled") or "true",
        "talk_frequency": database.get_setting("talk_frequency") or "4",
        "talk_guest_songs_per_set": database.get_setting("talk_guest_songs_per_set") or "4",
        "talk_segment_cooldown": database.get_setting("talk_segment_cooldown") or "2",
        "talk_caller_heartbroken_enabled": database.get_setting("talk_caller_heartbroken_enabled") or "false",
        "talk_caller_melancholy_enabled": database.get_setting("talk_caller_melancholy_enabled") or "false",
        "talk_volume": database.get_setting("talk_volume") or "100",
    }


@app.post("/api/talk/config")
def update_talk_config(body: dict):
    """Update talk show settings in bulk."""
    allowed = {
        "talk_segments_enabled", "talk_caller_enabled", "talk_caller_mode",
        "talk_monologue_enabled", "talk_guest_takeover_enabled",
        "talk_frequency", "talk_guest_songs_per_set", "talk_segment_cooldown",
        "talk_caller_heartbroken_enabled", "talk_caller_melancholy_enabled",
        "talk_volume",
    }
    for key, value in body.items():
        if key in allowed:
            database.set_setting(key, str(value))
            if key == "talk_segments_enabled" and str(value).lower() != "true":
                from announcer.talk_segments import reset_talk_counters
                reset_talk_counters()
    return {"status": "updated"}


@app.get("/api/talk/voice-status")
def talk_voice_status():
    """Check which TTS voices are available across all engines."""
    from announcer.voice_manager import get_voice_manager
    vm = get_voice_manager()
    status = vm.get_status()
    all_voices = status.get("all_voices", {})

    # Build per-engine voice status
    voices = {}
    for engine_id, voice_list in all_voices.items():
        for vid in voice_list:
            voices[f"{engine_id}:{vid}"] = vm.voice_downloaded(vid)

    return {
        "voices": voices,
        "engines": list(status.get("engine_ids", [])),
        "active_engine": status["active_engine"],
        "active_voice": status["active_voice"],
        "voice_mode": status.get("voice_mode", "user_select"),
        "gpu_available": status.get("gpu_available"),
        "omnivoice_ready": status.get("omnivoice_ready", False),
    }


@app.post("/api/talk/download-voices")
def talk_download_voices():
    """Download all Piper voice models for talk show personas."""
    from announcer.voice_manager import get_voice_manager
    vm = get_voice_manager()
    try:
        results = vm.download_all_voices()
        all_ok = all(results.values())
        database.set_setting("piper_downloaded", "true" if all_ok else "false")
        return {"status": "completed" if all_ok else "partial", "results": results}
    except Exception as e:
        logger.error(f"Voice download failed: {e}")
        return {"status": "failed", "error": str(e)}


@app.post("/api/tts/engine")
def set_tts_engine_endpoint(body: dict):
    """Switch TTS engine at runtime.

    Body: {"engine": "omnivoice" | "piper", "voice": "optional_voice_id"}
    """
    from announcer.voice_manager import get_voice_manager
    engine = body.get("engine")
    voice = body.get("voice")
    if not engine:
        return {"status": "error", "error": "Missing 'engine' field"}
    try:
        vm = get_voice_manager()
        vm.set_engine(engine, voice_id=voice)
        status = vm.get_status()
        return {
            "status": "ok",
            "active_engine": status["active_engine"],
            "active_voice": status["active_voice"],
            "gpu_available": status.get("gpu_available"),
        }
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Failed to set TTS engine: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/api/tts/voice-mode")
def set_voice_mode_endpoint(body: dict):
    """Set voice selection mode.

    Body: {"mode": "user_select" | "random" | "context"}
    """
    from announcer.voice_manager import get_voice_manager
    mode = body.get("mode")
    if not mode:
        return {"status": "error", "error": "Missing 'mode' field"}
    try:
        vm = get_voice_manager()
        vm.set_voice_mode(mode)
        database.set_setting("tts_voice_mode", mode)
        return {"status": "ok", "voice_mode": mode}
    except Exception as e:
        logger.error(f"Failed to set voice mode: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/api/tts/voice")
def set_tts_voice_endpoint(body: dict):
    """Set the active TTS voice.

    Body: {"voice": "The Trusted Morning Host"} or {"voice": "amy"}
    """
    from announcer.voice_manager import get_voice_manager
    voice = body.get("voice")
    if not voice:
        return {"status": "error", "error": "Missing 'voice' field"}
    try:
        vm = get_voice_manager()
        vm.set_voice(voice)
        return {"status": "ok", "active_voice": voice}
    except Exception as e:
        logger.error(f"Failed to set voice: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/api/talk/guest-dismiss")
def talk_dismiss_guest():
    """Dismiss the current guest DJ takeover (user voted them off)."""
    from announcer.talk_segments import dismiss_guest_takeover
    outro = dismiss_guest_takeover()
    if outro:
        qm = _get_queue_manager()
        with qm._condition:
            qm._pending_talk_segments.insert(0, outro.to_queue_item())
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        return {"status": "dismissed", "outro": True}
    return {"status": "no_active_guest"}


@app.get("/api/talk/personas")
def talk_personas():
    """List all available personas."""
    from announcer.personas import PERSONAS
    return {
        "personas": {
            pid: {
                "name": p.name,
                "voice_id": p.voice_id,
                "has_slot_bias": p.slot_bias is not None,
            }
            for pid, p in PERSONAS.items()
        }
    }


# ============================================================
# Onboarding Endpoints
# ============================================================

class OnboardRequest(BaseModel):
    song_ids: list


@app.get("/api/onboarding/songs")
def onboarding_songs():
    """Get neutral songs for the 'seed your taste' onboarding screen."""
    songs = database.get_neutral_centroid_songs(20)
    return {"songs": [dict(s) for s in songs]}


@app.post("/api/onboarding/complete")
def onboarding_complete(req: OnboardRequest):
    """Complete onboarding by recording taste seed signals."""
    global _current_session_id
    session_id = _ensure_session()

    for song_id in req.song_ids:
        # Record onboard heart signal
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal="onboard_heart",
            completion_pct=100.0,
        )
        # Also increment replay count to boost the song
        database.increment_replay_count(song_id)

    # Mark onboarding as complete
    database.set_setting("onboarding_complete", "true")

    # Start playing the first selected song
    if req.song_ids:
        song = database.get_song(req.song_ids[0])
        if song and song.get("filepath") and os.path.exists(song["filepath"]):
            p = player_engine.get_player()
            p.play_song(req.song_ids[0], song["filepath"], session_id)

    return {
        "status": "complete",
        "selected_count": len(req.song_ids),
        "session_id": session_id,
    }


@app.post("/api/onboarding/skip")
def onboarding_skip():
    """Skip onboarding - mark as complete with empty selection."""
    database.set_setting("onboarding_complete", "true")
    return {"status": "skipped"}


class OnboardingRefreshRequest(BaseModel):
    exclude_ids: list = []


@app.post("/api/onboarding/refresh")
def onboarding_refresh(req: OnboardingRefreshRequest):
    """Get new neutral song suggestions excluding already-shown songs."""
    exclude_ids = req.exclude_ids or []
    remaining = len(database.get_neutral_centroid_songs_excluding(9999, exclude_ids))
    songs = database.get_neutral_centroid_songs_excluding(20, exclude_ids)
    return {"songs": [dict(s) for s in songs], "remaining": remaining}


# ============================================================
# Command Endpoint
# ============================================================

def _execute_command_actions(actions: list, explanation: str,
                             p, qm, msm, song_id, session_id) -> dict:
    """Execute a list of tool-calling actions and return combined response."""
    results = []
    for act in actions:
        act_action = act.get("action")

        if act_action == "add_to_queue":
            song_ids = act.get("song_ids", [])
            position = act.get("position", "back")
            res = qm.add_manual(song_ids, position)
            added_ids = res["added"]
            skipped_ids = res["skipped"]
            entry = {"action": "add_to_queue", "song_ids": added_ids, "count": len(added_ids)}
            if skipped_ids:
                entry["skipped"] = skipped_ids
                entry["skipped_count"] = len(skipped_ids)
            results.append(entry)

        elif act_action == "play_song":
            sid = act.get("song_id")
            if sid:
                song = database.get_song(sid)
                if song and song.get("filepath") and os.path.exists(song["filepath"]):
                    database.record_play_event(song_id=sid, session_id=session_id, signal="playing")
                    p.play_song(sid, song["filepath"], session_id)
                    qm.on_song_started(sid, session_id)
                    results.append({"action": "play_song", "song_id": sid, "artist": song.get("artist"), "title": song.get("title")})

        elif act_action == "vibe_lock":
            enabled = act.get("enabled", True)
            if enabled:
                cs_id = p.get_current_song_id()
                if cs_id:
                    text_emb, audio_emb = _fetch_song_embeddings(cs_id)
                    msm.activate_vibe_lock(cs_id, text_emb, audio_embedding=audio_emb)
                    broadcast("vibe_lock_activated", {"song_id": cs_id})
                    results.append({"action": "vibe_lock", "enabled": True})
            else:
                msm.deactivate_vibe_lock()
                broadcast("vibe_lock_deactivated", {})
                results.append({"action": "vibe_lock", "enabled": False})

        elif act_action == "mood_shift":
            target = act.get("target_mood", "neutral")
            if msm.set_state(target):
                broadcast("mood_changed", {"state": target, "reason": "command"})
                database.record_mood_change(session_id, target, source="command")
                results.append({"action": "mood_shift", "target_mood": target})

        elif act_action == "set_filter":
            if "genre" in act:
                database.set_setting("genre_filter", act["genre"])
                results.append({"action": "genre_filter", "genre": act["genre"]})
            if "artist" in act:
                database.set_setting("artist_filter", act["artist"])
                results.append({"action": "artist_filter", "artist": act["artist"]})

        elif act_action == "clear_filters":
            database.set_setting("genre_filter", "")
            database.set_setting("artist_filter", "")
            results.append({"action": "clear_filters"})

        elif act_action == "exploration":
            level_map = {"low": 0.1, "medium": 0.5, "high": 0.9}
            level = level_map.get(act.get("level", "medium"), 0.5)
            database.set_setting("exploration_level", str(level))
            results.append({"action": "exploration", "level": act.get("level", "medium")})

        elif act_action == "clear_queue":
            qm.clear_queue()
            results.append({"action": "clear_queue"})

        elif act_action == "skip":
            skip_result = queue_skip()
            results.append({"action": "skip", **skip_result})

        elif act_action == "pause":
            results.append(pause_playback())

        elif act_action == "resume":
            results.append(resume_playback())

        elif act_action == "heart":
            results.append(heart_current())

        elif act_action == "thumbs_down":
            results.append(thumbs_down_current())

        elif act_action == "sleep_timer":
            minutes = act.get("minutes", 30)
            st = _get_sleep_timer()
            st.start(minutes, fade_seconds=30, switch_to_chill=True)
            results.append({"action": "sleep_timer", "minutes": minutes})

    total_skipped = sum(r.get("skipped_count", 0) for r in results)
    if total_skipped > 0:
        explanation += f" ({total_skipped} song{'s' if total_skipped > 1 else ''} already in queue)"

    return {
        "actions": results,
        "explanation": explanation,
    }


@app.post("/api/command")
def handle_command(req: CommandRequest):
    """Handle natural language commands."""
    global _current_session_id
    p = player_engine.get_player()
    qm = _get_queue_manager()
    msm = _get_mood_state_machine()

    # Build context
    song_id = p.get_current_song_id()
    current_song = dict(database.get_song(song_id)) if song_id else None
    msm_state = msm.get_state()
    archetype_name, _ = archetype_engine.detect_archetype()

    queue = []
    with qm._condition:
        for item in list(qm._queue)[:3]:
            queue.append({
                "song_id": item.get("song_id"),
                "artist": item.get("artist", ""),
                "title": item.get("title", ""),
            })

    context = {
        "current_song": current_song,
        "mood_state": msm_state,
        "queue": queue,
        "archetype": archetype_name,
    }

    # Parse the command
    from commands import parser as cmd_parser
    parsed = cmd_parser.parse_command(req.text, context)

    # Handle multi-step actions from tool-calling
    if "actions" in parsed and parsed["actions"]:
        return _execute_command_actions(
            parsed["actions"], parsed.get("explanation", "Done."),
            p, qm, msm, song_id, _current_session_id,
        )

    action = parsed.get("action")

    # Execute the action
    if action == "mood_shift":
        target = parsed.get("target_mood", msm_state)
        if msm.set_state(target):
            broadcast("mood_changed", {"state": target, "reason": "command"})
            database.record_mood_change(_current_session_id, target, source="command")
            return {"action": action, "target_mood": target, "explanation": parsed["explanation"]}
        return {"action": action, "explanation": f"Could not set mood to {target}"}

    elif action == "vibe_lock":
        if not song_id:
            return {"action": action, "explanation": "No song playing to lock"}
        text_emb, audio_emb = _fetch_song_embeddings(song_id)
        msm.activate_vibe_lock(song_id, text_emb, audio_embedding=audio_emb)
        broadcast("vibe_lock_activated", {"song_id": song_id})
        return {"action": action, "explanation": parsed["explanation"]}

    elif action == "vibe_unlock":
        msm.deactivate_vibe_lock()
        broadcast("vibe_lock_deactivated", {})
        return {"action": action, "explanation": parsed["explanation"]}

    elif action == "replay":
        result = queue_replay()
        if isinstance(result, dict) and result.get("status") == "replaying":
            return {"action": action, "explanation": parsed["explanation"]}
        return {"action": action, "explanation": "No song to replay"}

    elif action == "skip":
        result = queue_skip()
        if isinstance(result, dict) and result.get("status") == "skipped":
            return {"action": action, "explanation": parsed["explanation"], "next": result.get("next")}
        return {"action": action, "explanation": "Skip failed"}

    elif action == "undo_skip":
        result = undo_skip()
        return {"action": action, "explanation": "Going back" if result.get("success") else "Nothing to undo", **result}

    elif action == "heart":
        return heart_current()

    elif action == "thumbs_down":
        return thumbs_down_current()

    elif action == "pause":
        return pause_playback()

    elif action == "resume":
        return resume_playback()

    elif action == "volume_up":
        current_vol = p.get_volume() or 70
        new_vol = min(100, current_vol + 15)
        p.set_volume(new_vol)
        return {"action": action, "volume": new_vol, "explanation": f"Volume up to {new_vol}"}

    elif action == "volume_down":
        current_vol = p.get_volume() or 70
        new_vol = max(0, current_vol - 15)
        p.set_volume(new_vol)
        return {"action": action, "volume": new_vol, "explanation": f"Volume down to {new_vol}"}

    elif action == "set_volume":
        level = parsed.get("level")
        if level is None:
            return {"action": action, "explanation": "What volume? Try 'volume to 50'"}
        level = max(0, min(100, int(level)))
        p.set_volume(level)
        return {"action": action, "volume": level, "explanation": f"Volume set to {level}"}

    elif action == "clear_queue":
        with qm._condition:
            qm._queue.clear()
        broadcast("queue_updated", {"queue": []})
        return {"action": action, "explanation": parsed["explanation"]}

    elif action == "rebuild":
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "explanation": "Rebuilding queue..."}

    elif action == "mode_switch":
        mode = parsed.get("mode")
        if mode is None:
            current_mode = database.get_setting("current_mode") or "radio"
            mode = "normal" if current_mode == "radio" else "radio"
        database.set_setting("current_mode", mode)
        with qm._condition:
            qm._queue.clear()
        broadcast("queue_updated", {"queue": []})
        return {"action": action, "mode": mode, "explanation": f"Switched to {mode} mode"}

    elif action == "exploration":
        level_str = parsed.get("level", "medium")
        level_map = {"low": 0.1, "medium": 0.5, "high": 0.9}
        level = level_map.get(level_str, 0.5)
        database.set_setting("exploration_level", str(level))
        return {"action": action, "level": level_str, "explanation": parsed["explanation"]}

    elif action == "genre_filter":
        genre = parsed.get("genre", "")
        database.set_setting("genre_filter", genre)
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "genre": genre, "explanation": parsed["explanation"]}

    elif action == "crossmodal_search":
        query = parsed.get("query", "")
        if not query:
            return {"action": action, "explanation": "What sound are you looking for? Try 'sounds like midnight driving'"}
        if not config.MUQ_ENABLED:
            return {"action": action, "explanation": "Audio search not available (MuQ disabled)"}
        try:
            from llm.muq_engine import is_available, embed_texts
            if not is_available():
                return {"action": action, "explanation": "Audio embedding model not available"}
            text_embeds = embed_texts([query])
            if not text_embeds:
                return {"action": action, "explanation": "Failed to process search query"}
            from engine.retriever import get_chroma_collection
            audio_collection = get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
            results = audio_collection.query(
                query_embeddings=text_embeds[0],
                n_results=10,
                include=["distances", "metadatas"],
            )
            songs = []
            for i in range(len(results["ids"][0])):
                sid = int(results["ids"][0][i])
                dist = results["distances"][0][i]
                meta = results["metadatas"][0][i]
                score = 1.0 - dist
                song = database.get_song(sid)
                if song:
                    songs.append({
                        "song_id": sid,
                        "score": round(score, 4),
                        "artist": song.get("artist", ""),
                        "title": song.get("title", ""),
                        "energy": float(meta.get("energy", 0.5)),
                        "valence": float(meta.get("valence", 0.5)),
                    })
            return {
                "action": action,
                "songs": songs,
                "query": query,
                "explanation": f"Found {len(songs)} songs matching '{query}'",
            }
        except Exception as e:
            return {"action": action, "explanation": f"Search failed: {e}"}

    elif action == "artist_filter":
        artist = parsed.get("artist", "")
        database.set_setting("artist_filter", artist)
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "artist": artist, "explanation": parsed["explanation"]}

    elif action == "folder_filter":
        folders_raw = parsed.get("folders", "")
        if isinstance(folders_raw, str):
            # Parse folder name from command text: match against known subfolders
            known = database.get_subfolders()
            folders_raw_lower = folders_raw.lower()
            matched = [f for f in known if folders_raw_lower in f.lower()]
            if matched:
                database.set_setting("folder_filter", json.dumps(matched))
                qm.on_song_started(song_id, _current_session_id)
                return {"action": action, "folders": matched, "explanation": f"Restricted to {', '.join(matched)}"}
            return {"action": action, "explanation": f"No folder matching '{folders_raw}' found"}
        return {"action": action, "explanation": "Could not parse folder filter"}

    elif action == "clear_filters":
        database.set_setting("genre_filter", "")
        database.set_setting("artist_filter", "")
        database.set_setting("folder_filter", "")
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "explanation": "Filters cleared"}

    elif action == "play_song":
        query = parsed.get("query", "")
        if not query:
            # Bare "play" with no query → treat as resume
            return resume_playback()
        conn = database.get_connection()
        try:
            like = f"%{query.lower()}%"
            row = conn.execute(
                "SELECT id, filepath FROM songs WHERE LOWER(title) LIKE ? OR LOWER(artist) LIKE ? ORDER BY play_count DESC LIMIT 1",
                (like, like),
            ).fetchone()
        finally:
            conn.close()
        if row and row["filepath"] and os.path.exists(row["filepath"]):
            session_id = _ensure_session()
            database.record_play_event(
                song_id=row["id"],
                session_id=session_id,
                signal="playing",
            )
            p.play_song(row["id"], row["filepath"], session_id)
            qm.on_song_started(row["id"], session_id)
            return {"action": action, "song_id": row["id"], "explanation": f"Playing: {query}"}
        return {"action": action, "explanation": f"No song found matching '{query}'"}

    elif action == "queue_info":
        with qm._condition:
            next_item = qm._queue[0] if qm._queue else None
        if next_item:
            return {
                "action": action,
                "next_song": f"{next_item.get('artist', '?')} - {next_item.get('title', '?')}",
                "queue_size": len(qm._queue),
                "explanation": f"Next up: {next_item.get('artist', '?')} - {next_item.get('title', '?')}",
            }
        return {"action": action, "explanation": "Queue is empty"}

    elif action == "why_this_song":
        if not current_song:
            return {"action": action, "explanation": "No song playing"}
        return {
            "action": action,
            "song": current_song,
            "explanation": parsed["explanation"],
        }

    elif action == "song_info":
        if current_song:
            return {
                "action": action,
                "song": current_song,
                "explanation": parsed["explanation"],
            }
        return {"action": action, "explanation": "No song playing"}

    elif action == "stop":
        p.stop()
        _close_current_session()
        return {"action": action, "explanation": "Playback stopped"}

    elif action == "help":
        from commands.parser import get_help_categories
        return {
            "action": "help",
            "explanation": parsed["explanation"],
            "commands": get_help_categories(),
        }

    elif action == "sleep_timer":
        minutes = parsed.get("minutes", 30)
        st = _get_sleep_timer()
        st.start(minutes, fade_seconds=30, switch_to_chill=True)
        return {"action": action, "minutes": minutes, "explanation": f"Sleep timer: {minutes} minutes"}

    elif action == "cancel_sleep":
        st = _get_sleep_timer()
        st.cancel()
        return {"action": action, "explanation": "Sleep timer cancelled"}

    elif action == "talk_show":
        enabled = parsed.get("enabled", "true")
        enabled_str = str(enabled).lower()
        allowed = {"talk_segments_enabled", "talk_caller_enabled", "talk_monologue_enabled",
                    "talk_guest_takeover_enabled"}
        for key in allowed:
            database.set_setting(key, enabled_str)
        if enabled_str != "true":
            from announcer.talk_segments import reset_talk_counters
            reset_talk_counters()
        return {
            "action": action,
            "enabled": enabled_str == "true",
            "explanation": f"Talk show {'enabled' if enabled_str == 'true' else 'disabled'}",
        }

    elif action == "custom":
        return {"action": action, "explanation": parsed["explanation"]}

    elif action == "set_filter":
        results = []
        if "genre" in parsed:
            genre = parsed["genre"]
            database.set_setting("genre_filter", genre)
            results.append(f"genre={genre}")
        if "artist" in parsed:
            artist = parsed["artist"]
            database.set_setting("artist_filter", artist)
            results.append(f"artist={artist}")
        if results:
            qm.on_song_started(song_id, _current_session_id)
            return {"action": "set_filter", "changes": results, "explanation": "Filter applied: " + ", ".join(results)}
        return {"action": action, "explanation": "No filter specified"}

    else:
        return {
            "action": action,
            "explanation": f"Unknown action: {action}. Try 'help' for available commands.",
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT)
