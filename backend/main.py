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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Form
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

import config

from db import database
from ingest import pipeline
from engine import player as player_engine
from engine import queue as queue_engine
from engine import mood as mood_engine
from engine import archetype as archetype_engine
from engine import energy as energy_engine

logging.basicConfig(level=logging.DEBUG)
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
    # Reset stale genre/artist filters from testing
    if database.get_setting("genre_filter") == "jazz":
        database.set_setting("genre_filter", "")
    print(f"Database initialized at {config.DB_PATH}")
    print(f"Music directory: {config.MUSIC_DIR}")
    print(f"Acestep JSON directory: {config.ACESTEP_JSON_DIR}")

    # Clear stale TTS cache files on startup
    tts_cache_dir = os.path.join(config.BASE_DIR, "data", "tts_cache")
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

    def _on_song_ended(song_id):
        _playback_logger.debug("=== main._on_song_ended(song=%s) ENTER — running in background thread", song_id)
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
                _playback_logger.debug("  <- play_next done")
        else:
            _playback_logger.warning("  -> qm.pop_next() returned None — nothing to play!")
        _playback_logger.debug("=== main._on_song_ended EXIT")

    p.on_song_ended(_on_song_ended)

    # Pre-build recommendation queue on startup
    def _prebuild_queue():
        """Start queue rebuild in background after app is ready."""
        time.sleep(2)
        try:
            qm.pop_next()
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


# WebSocket clients
connected_clients: list[WebSocket] = []


class MoodSetRequest(BaseModel):
    state: str


class VibeLockActivateRequest(BaseModel):
    song_id: int


class CommandRequest(BaseModel):
    text: str


class SeekRequest(BaseModel):
    ms: int = 0


class VolumeRequest(BaseModel):
    volume: int = 70


class BlacklistRequest(BaseModel):
    reason: str = "user"


class TagsRequest(BaseModel):
    user_tags: list | None = None
    user_notes: str | None = None


class ReorderRequest(BaseModel):
    order: list[int | None]


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
        clients_snapshot = list(connected_clients)
        for client in clients_snapshot:
            async with _broadcast_write_lock:
                try:
                    await asyncio.wait_for(client.send_json(message), timeout=3.0)
                except Exception as e:
                    logger.warning("WebSocket send failed: %s", e)
                    disconnected.append(client)
        for client in disconnected:
            if client in connected_clients:
                connected_clients.remove(client)
        _broadcast_queue.task_done()


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
    connected_clients.append(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(connected_clients)}")
    try:
        while True:
            # Keep connection alive; client can send ping messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(connected_clients)}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# Global engine instances
_queue_manager = None
_mood_state_machine = None
_singleton_lock = threading.RLock()

# RMS envelope cache for audio-reactive waveform
_rms_cache = {}  # {song_id: [float, ...]} — 100-bin normalized envelope
_current_rms_song_id = None


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
):
    conn = database.get_connection()
    try:
        # Build WHERE clause for search
        where = ""
        params = []
        if search:
            where = "WHERE (LOWER(title) LIKE ? OR LOWER(artist) LIKE ? OR LOWER(lyrics) LIKE ?)"
            params.append(f"%{search.lower()}%")
            params.append(f"%{search.lower()}%")
            params.append(f"%{search.lower()}%")

        # Build ORDER BY
        valid_sort = {"title", "artist", "energy", "valence", "bpm", "id"}
        if sort_by not in valid_sort:
            sort_by = "title"
        dir_ = "DESC" if sort_dir == "desc" else "ASC"
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


@app.post("/api/songs/{song_id:int}/blacklist")
def blacklist_song(song_id: int, req: BlacklistRequest = None):
    """Add a song to the blacklist."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    reason = req.reason if req else "user"
    database.blacklist_song(song_id, reason)
    broadcast("song_blacklisted", {"song_id": song_id})
    return {"status": "blacklisted", "song_id": song_id}


@app.delete("/api/songs/{song_id:int}/blacklist")
def unblacklist_song(song_id: int):
    """Remove a song from the blacklist."""
    database.unblacklist_song(song_id)
    return {"status": "unblacklisted", "song_id": song_id}


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


# ============================================================
# Play Endpoints
# ============================================================

_current_session_id = None


def _ensure_session():
    global _current_session_id
    if not _current_session_id:
        msm = _get_mood_state_machine()
        _current_session_id = database.create_session(initial_mood=msm.get_state())
    return _current_session_id


def _close_current_session():
    global _current_session_id
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
        for item in list(qm._queue)[:4]:
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
        p.resume()
        return {"status": "playing", "action": "resumed"}

    # Stopped — try to restart current song
    song_id = p.get_current_song_id()
    if song_id:
        song = database.get_song(song_id)
        if song and song.get("filepath") and os.path.exists(song["filepath"]):
            session_id = _ensure_session()
            success = p.play_song(song_id, song["filepath"], session_id)
            if success:
                qm = _get_queue_manager()
                qm.on_song_started(song_id, session_id)
                broadcast("song_started", {"song_id": song_id, "session_id": session_id})
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
            broadcast("song_started", {"song_id": pick["song_id"], "session_id": session_id})
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


@app.post("/api/play/next")
def play_next():
    p = player_engine.get_player()
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
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal="thumbed_down",
            mood_state=msm.get_state(),
        )
        qm.reset_skip_streak()
        broadcast("song_thumbed_down", {"song_id": song_id})
    return {"status": "thumbed_down", "song_id": song_id}


@app.post("/api/play/stop")
def stop_playback():
    global _current_session_id
    p = player_engine.get_player()
    p.stop()
    _current_session_id = None
    return {"status": "stopped"}


# ============================================================
# Queue Endpoints
# ============================================================

@app.get("/api/queue")
def get_queue():
    qm = _get_queue_manager()
    with qm._condition:
        return list(qm._queue)


@app.get("/api/queue/status")
def get_queue_status():
    """Return queue status including rebuilding state."""
    qm = _get_queue_manager()
    with qm._condition:
        return {
            "rebuilding": qm._rebuilding,
            "queue_size": len(qm._queue),
            "queue": qm._get_display_queue(),
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
        if qm._queue:
            return qm._queue[0]
        return {"error": "Queue is empty"}


@app.post("/api/queue/clear")
def queue_clear():
    """Clear the recommendation queue."""
    qm = _get_queue_manager()
    with qm._condition:
        qm._queue.clear()
    broadcast("queue_updated", {"queue": []})
    return {"status": "queue_cleared"}


@app.post("/api/queue/rebuild")
def queue_rebuild():
    """Force a queue rebuild in the background."""
    qm = _get_queue_manager()
    threading.Thread(target=qm._rebuild_queue, daemon=True).start()
    return {"status": "rebuild_started"}


@app.post("/api/queue/add/{song_id:int}")
def queue_add(song_id: int):
    """Add a song to the end of the queue without playing it."""
    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}
    qm = _get_queue_manager()
    with qm._condition:
        qm._queue.append({
            "song_id": song_id,
            "slot": "manual",
            "reason": "Added by you.",
            "artist": song.get("artist", ""),
            "title": song.get("title", ""),
        })
        qm._condition.notify_all()
    broadcast("queue_updated", {"queue": qm._get_display_queue()})
    return {"status": "added", "song_id": song_id}


@app.put("/api/queue/reorder")
def queue_reorder(req: ReorderRequest):
    """Reorder the queue by song_id list."""
    order = req.order
    if not order:
        return {"error": "No order provided"}
    qm = _get_queue_manager()
    with qm._condition:
        item_map = {item.get("song_id"): item for item in qm._queue if item.get("song_id") is not None}
        new_queue = []
        for sid in order:
            if sid in item_map:
                new_queue.append(item_map[sid])
        for item in qm._queue:
            if item.get("song_id") not in order:
                new_queue.append(item)
        qm._queue = new_queue
        qm._condition.notify_all()
    broadcast("queue_updated", {"queue": qm._get_display_queue()})
    return {"status": "reordered"}


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

    if song_id:
        session_id = p._current_session_id if hasattr(p, '_current_session_id') else _current_session_id
        database.record_play_event(
            song_id=song_id,
            session_id=session_id,
            signal="skip_early",
            mood_state=msm.get_state(),
        )
        broadcast("song_skipped", {"song_id": song_id})

    # Increment skip streak
    qm._skip_streak += 1

    # Check for auto mood shift on skip streak
    mood_shifted = False
    if qm._skip_streak >= 3:
        new_state = msm.check_auto_mood_shift([], qm._skip_streak)
        if new_state:
            msm.set_state(new_state)
            mood_shifted = True
            broadcast("mood_changed", {"state": new_state, "reason": f"Skip streak ({qm._skip_streak})"})
            database.record_mood_change(_current_session_id, new_state, source="auto")

    # Stop current and pop next
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

    # If mood shifted, don't auto-play — let the current song finish so the
    # next queue rebuild picks songs matching the new mood.
    if mood_shifted:
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        return {
            "status": "skipped",
            "mood_shift": True,
            "next": pick,
            "skip_streak": qm._skip_streak,
        }

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
        broadcast("queue_updated", {"queue": qm._get_display_queue()})
        return {
            "status": "skipped",
            "next": pick,
            "skip_streak": qm._skip_streak,
        }
    return {"status": "skipped", "skip_streak": qm._skip_streak}


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
    """Activate vibe lock for a specific song."""
    msm = _get_mood_state_machine()
    song = database.get_song(req.song_id)
    if not song:
        return {"error": "Song not found"}

    # Get embedding from ChromaDB
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    collection = client.get_collection("songs")
    result = collection.get(ids=[str(req.song_id)], include=["embeddings"])
    embedding = result["embeddings"][0] if result["embeddings"] else None

    if embedding:
        msm.activate_vibe_lock(req.song_id, embedding)
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
        unique_songs = conn.execute("SELECT COUNT(DISTINCT song_id) as cnt FROM play_events").fetchone()["cnt"]
        total_songs = conn.execute("SELECT COUNT(*) as cnt FROM songs").fetchone()["cnt"]

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

        return {
            "total_plays": total_plays,
            "completed": completed,
            "skipped": skipped,
            "hearted": hearted,
            "unique_songs_played": unique_songs,
            "total_songs_in_library": total_songs,
            "completion_rate": round(completed / max(1, total_plays), 3),
            "skip_rate": round(skipped / max(1, total_plays), 3),
            "avg_completion_rate": round(avg_comp, 1),
            "top_artists": [{"artist": r["artist"], "plays": r["cnt"]} for r in top_artists],
            "top_songs": [{"artist": r["artist"], "title": r["title"], "plays": r["cnt"]} for r in top_songs],
            "by_hour": [{"hour": r["hour_of_day"], "count": r["cnt"]} for r in by_hour],
            "by_mood": [{"mood": r["mood_state"], "count": r["cnt"]} for r in by_mood],
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
            """SELECT pe.*, s.artist, s.title, s.duration_secs FROM play_events pe
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

        # Listening time (sum of durations of completed songs)
        listen_secs = sum(
            (e["duration_secs"] or 0) for e in events if e["signal"] in ("completed", "skip_early", "skip_late")
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

        # Mood distribution
        mood_counts = {}
        for e in events:
            m = e["mood_state"]
            if m:
                mood_counts[m] = mood_counts.get(m, 0) + 1

        return {
            "date": today,
            "total_songs": total,
            "unique_songs": unique,
            "completed": completed,
            "skipped": skipped,
            "hearted": hearted,
            "listen_minutes": round(listen_secs / 60, 1),
            "completion_rate": round(completed / max(1, total), 3),
            "top_artists": [{"artist": a, "plays": c} for a, c in top_artists],
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


@app.get("/api/songs/{song_id:int}/similar")
def similar_songs(song_id: int, limit: int = 10):
    """Find songs similar to a given song using ChromaDB embedding search."""
    from engine.retriever import get_chroma_collection
    collection = get_chroma_collection()
    try:
        results = collection.get(
            ids=[str(song_id)],
            include=["embeddings"],
        )
        if not results["embeddings"] or not results["embeddings"][0]:
            return {"error": "Song has no embedding"}
        embedding = results["embeddings"][0]

        # Query for similar songs
        similar = collection.query(
            query_embeddings=[embedding],
            n_results=limit + 1,  # +1 to exclude self
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
            # Get full song info from SQLite
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
            if len(songs) >= limit:
                break
        return songs
    except Exception as e:
        return {"error": str(e)}


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


# ============================================================
# LLM Status
# ============================================================

@app.get("/api/llm/status")
def llm_status():
    """Return health status of chat and embed providers."""
    result = {"chat": {"provider": "uninitialized", "model": None, "healthy": False},
              "embed": {"provider": "uninitialized", "model": None, "healthy": False}}
    try:
        from llm import llm_chat
        chat_client = llm_chat()
        from llm import get_active_provider_name
        provider_name = get_active_provider_name()
        result["chat"]["provider"] = provider_name
        result["chat"]["model"] = config.OLLAMA_CHAT_MODEL if "Ollama" in provider_name else config.OPENAI_CHAT_MODEL
        result["chat"]["healthy"] = True
    except Exception as e:
        result["chat"]["error"] = str(e)

    try:
        from llm import llm_embed
        llm_embed()
        from llm import get_active_provider_name
        provider_name = get_active_provider_name()
        result["embed"]["provider"] = provider_name
        result["embed"]["model"] = config.OLLAMA_EMBED_MODEL if "Ollama" in provider_name else config.OPENAI_EMBED_MODEL
        result["embed"]["healthy"] = True
    except Exception as e:
        result["embed"]["error"] = str(e)
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
    # Persist archetype
    database.set_setting("archetype", archetype_name)
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
            ready = next_song_id in qm._pregen_cache

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
    }


@app.post("/api/talk/config")
def update_talk_config(body: dict):
    """Update talk show settings in bulk."""
    allowed = {
        "talk_segments_enabled", "talk_caller_enabled", "talk_caller_mode",
        "talk_monologue_enabled", "talk_guest_takeover_enabled",
        "talk_frequency", "talk_guest_songs_per_set", "talk_segment_cooldown",
        "talk_caller_heartbroken_enabled", "talk_caller_melancholy_enabled",
    }
    for key, value in body.items():
        if key in allowed:
            database.set_setting(key, str(value))
            if key == "talk_segments_enabled" and value != "true":
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


# ============================================================
# Command Endpoint
# ============================================================

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
        msm.activate_vibe_lock(song_id, None)
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

    elif action == "clear_queue":
        with qm._condition:
            qm._queue.clear()
        broadcast("queue_updated", {"queue": []})
        return {"action": action, "explanation": parsed["explanation"]}

    elif action == "exploration":
        level_str = parsed.get("level", "medium")
        level_map = {"low": 0.1, "medium": 0.5, "high": 0.9}
        level = level_map.get(level_str, 0.5)
        database.set_setting("exploration_level", str(level))
        return {"action": action, "level": level_str, "explanation": parsed["explanation"]}

    elif action == "genre_filter":
        genre = parsed.get("genre", "")
        database.set_setting("genre_filter", genre)
        # Rebuild queue with genre filter
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "genre": genre, "explanation": parsed["explanation"]}

    elif action == "artist_filter":
        artist = parsed.get("artist", "")
        database.set_setting("artist_filter", artist)
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "artist": artist, "explanation": parsed["explanation"]}

    elif action == "clear_filters":
        database.set_setting("genre_filter", "")
        database.set_setting("artist_filter", "")
        qm.on_song_started(song_id, _current_session_id)
        return {"action": action, "explanation": "Filters cleared"}

    elif action == "play_song":
        query = parsed.get("query", "")
        if not query:
            return {"action": action, "explanation": "What song do you want to play?"}
        # Search for matching song
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
        return {
            "action": "help",
            "explanation": parsed["explanation"],
            "commands": [
                "mood: 'more energetic', 'chill vibes', 'focus mode', 'melancholy', 'discover something new'",
                "vibe: 'vibe lock', 'vibe unlock'",
                "queue: 'what's next', 'replay', 'skip', 'clear queue'",
                "exploration: 'get more experimental', 'play it safe'",
                "genre: 'play more jazz', 'more rock'",
                "artist: 'play more Radiohead'",
                "filter: 'clear filters' to remove genre/artist filters",
                "play: 'play <song name>' to find and play a specific song",
                "info: 'why this song', 'tell me about this'",
                "control: 'stop', 'pause', 'help'",
            ],
        }

    else:
        return {
            "action": action,
            "explanation": f"Unknown action: {action}. Try 'help' for available commands.",
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT)
