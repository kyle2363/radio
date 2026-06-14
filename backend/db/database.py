import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from rapidfuzz import fuzz

import config

DB_PATH = config.DB_PATH

# --- Settings cache (Issue 3: avoid DB reads in hot paths) ---
_settings_cache = {}
_settings_cache_lock = threading.Lock()
_SETTINGS_CACHE_TTL = 5  # seconds


def _get_cached_setting(key):
    with _settings_cache_lock:
        entry = _settings_cache.get(key)
        if entry and time.time() - entry["ts"] < _SETTINGS_CACHE_TTL:
            return entry["val"]
    val = get_setting(key)
    with _settings_cache_lock:
        _settings_cache[key] = {"val": val, "ts": time.time()}
    return val


def _invalidate_setting(key):
    with _settings_cache_lock:
        _settings_cache.pop(key, None)


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_connection_cm():
    """Context manager for database connections (auto-closes)."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = get_connection()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    add_missing_columns()


def close_stale_sessions():
    """Close all sessions that don't have an ended_at timestamp (crashed sessions)."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
            (datetime.now().isoformat(),),
        )
        conn.commit()
        count = cursor.rowcount
        if count > 0:
            import logging
            logging.getLogger(__name__).info(f"Closed {count} stale session(s) on startup")
    finally:
        conn.close()


def add_missing_columns():
    """Add columns that may be missing from an older schema."""
    conn = get_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(songs)")
        columns = {row[1] for row in cursor.fetchall()}
        for col in ["keyscale", "timesignature", "vocal_language"]:
            if col not in columns:
                conn.execute(f"ALTER TABLE songs ADD COLUMN {col} TEXT")
        for col in ["ingest_error", "ingest_error_count", "rms_envelope"]:
            if col not in columns:
                if col == "ingest_error_count":
                    conn.execute(f"ALTER TABLE songs ADD COLUMN {col} INTEGER DEFAULT 0")
                else:
                    conn.execute(f"ALTER TABLE songs ADD COLUMN {col} TEXT")
        # Migration: add dj_effect_eligible column to songs
        if "dj_effect_eligible" not in columns:
            conn.execute("ALTER TABLE songs ADD COLUMN dj_effect_eligible INTEGER DEFAULT 1")

        # Migration: add subfolder column to songs
        if "subfolder" not in columns:
            conn.execute("ALTER TABLE songs ADD COLUMN subfolder TEXT")
            _populate_subfolders(conn)

        # Migration: add gain_db for volume normalization
        if "gain_db" not in columns:
            conn.execute("ALTER TABLE songs ADD COLUMN gain_db REAL DEFAULT 0.0")

        # Migration: add file_mtime to songs
        import os as _os
        if "file_mtime" not in columns:
            conn.execute("ALTER TABLE songs ADD COLUMN file_mtime REAL")
            _backfill_file_mtimes(conn)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_file_mtime ON songs(file_mtime)")
        except Exception:
            pass

        # Ensure subfolder is populated for all songs (migration may have missed new ones)
        if "subfolder" in columns:
            rows = conn.execute("SELECT id, filepath FROM songs WHERE subfolder IS NULL OR subfolder = '' LIMIT 1").fetchall()
            if rows:
                _populate_subfolders(conn)

        # Migration: add undone column to play_events
        cursor = conn.execute("PRAGMA table_info(play_events)")
        pe_columns = {row[1] for row in cursor.fetchall()}
        if "undone" not in pe_columns:
            conn.execute("ALTER TABLE play_events ADD COLUMN undone INTEGER DEFAULT 0")
        # Migration: add blacklist level columns
        cursor = conn.execute("PRAGMA table_info(blacklist)")
        bl_columns = {row[1] for row in cursor.fetchall()}
        for col in ["level", "expires_at", "mood", "artist", "cluster_ids"]:
            if col not in bl_columns:
                if col == "level":
                    conn.execute("ALTER TABLE blacklist ADD COLUMN level TEXT NOT NULL DEFAULT 'never_play'")
                else:
                    conn.execute(f"ALTER TABLE blacklist ADD COLUMN {col} TEXT")
        # Ensure the column-level index exists (safe after ALTER TABLE above)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_level ON blacklist(level)")
        except Exception as idx_e:
            import logging
            logging.getLogger(__name__).warning(f"Could not create blacklist level index: {idx_e}")

        # Migration: add duration_hours to skip_feedback
        cursor = conn.execute("PRAGMA table_info(skip_feedback)")
        sf_columns = {row[1] for row in cursor.fetchall()}
        if "duration_hours" not in sf_columns:
            conn.execute("ALTER TABLE skip_feedback ADD COLUMN duration_hours INTEGER")
        if "duration" not in sf_columns:
            conn.execute("ALTER TABLE skip_feedback ADD COLUMN duration TEXT")

        # Ensure song_suppression table exists (in case schema.sql wasn't re-run)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS song_suppression (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id     INTEGER REFERENCES songs(id),
                reason      TEXT,
                expires_at  DATETIME,
                session_id  INTEGER REFERENCES sessions(id),
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_song_suppression_song ON song_suppression(song_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_song_suppression_expires ON song_suppression(expires_at)")
        except Exception:
            pass

        # Seed new settings that may not exist in older databases
        new_settings = {
            'vibe_lock_duration_minutes': '30',
            'vibe_lock_max_songs': '10',
            'dislike_decay_rate': '0.029',
            'dj_speech_speed': '0.90',
            'dj_intro_start_hour': '6',
            'dj_intro_end_hour': '23',
        }
        for key, val in new_settings.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val),
            )

        # Ensure performance indexes exist
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_song ON play_events(song_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_session ON play_events(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_song_signal ON play_events(song_id, signal)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_played_at ON play_events(played_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_last_played ON songs(last_played)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_recovery_score ON songs(recovery_score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_avg_completion ON songs(avg_completion)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transitions_from ON transitions(from_song_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_history_session ON queue_history(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_history_song ON queue_history(song_id)")
        except Exception:
            pass

        conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Migration failed: {e}")
    finally:
        conn.close()


def _populate_subfolders(conn):
    """Extract subfolder from filepath and update existing songs that have NULL subfolder."""
    try:
        rows = conn.execute("SELECT id, filepath FROM songs WHERE subfolder IS NULL OR subfolder = ''").fetchall()
        updated = 0
        for row in rows:
            subfolder = _extract_subfolder_from_path(row["filepath"])
            if subfolder:
                conn.execute("UPDATE songs SET subfolder = ? WHERE id = ?", (subfolder, row["id"]))
                updated += 1
        if updated:
            conn.commit()
            import logging
            logging.getLogger(__name__).info(f"Populated subfolder for {updated} songs")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to populate subfolders: {e}")


def _backfill_file_mtimes(conn):
    """Populate file_mtime from os.stat for songs that have NULL file_mtime."""
    import os as _os
    try:
        rows = conn.execute("SELECT id, filepath FROM songs WHERE file_mtime IS NULL").fetchall()
        updated = 0
        for row in rows:
            try:
                mtime = _os.path.getmtime(row["filepath"])
                conn.execute("UPDATE songs SET file_mtime = ? WHERE id = ?", (mtime, row["id"]))
                updated += 1
            except (OSError, FileNotFoundError):
                pass
        if updated:
            conn.commit()
            import logging
            logging.getLogger(__name__).info(f"Backfilled file_mtime for {updated} songs")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to backfill file_mtimes: {e}")


def get_file_mtime_range():
    """Return (min_mtime, max_mtime) for songs with non-null mtime, or (None, None)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT MIN(file_mtime) as mn, MAX(file_mtime) as mx FROM songs WHERE file_mtime IS NOT NULL").fetchone()
        if row and row["mn"] is not None:
            return (row["mn"], row["mx"])
        return (None, None)
    finally:
        conn.close()


def get_song_ids_in_mtime_range(min_mtime, max_mtime):
    """Get song IDs with file_mtime within [min_mtime, max_mtime]."""
    conn = get_connection()
    try:
        if min_mtime is not None and max_mtime is not None:
            rows = conn.execute("SELECT id FROM songs WHERE file_mtime IS NOT NULL AND file_mtime >= ? AND file_mtime <= ?", (min_mtime, max_mtime)).fetchall()
        elif min_mtime is not None:
            rows = conn.execute("SELECT id FROM songs WHERE file_mtime IS NOT NULL AND file_mtime >= ?", (min_mtime,)).fetchall()
        elif max_mtime is not None:
            rows = conn.execute("SELECT id FROM songs WHERE file_mtime IS NOT NULL AND file_mtime <= ?", (max_mtime,)).fetchall()
        else:
            return []
        return [r["id"] for r in rows]
    finally:
        conn.close()


def _extract_subfolder_from_path(filepath):
    """Extract the subfolder name relative to the music directory from a filepath.

    E.g. '.../audio_files/song 40/file.mp3' → 'song 40'
    """
    if not filepath:
        return None
    import config
    music_dir = str(config.MUSIC_DIR).replace("\\", "/").rstrip("/")
    normalized = filepath.replace("\\", "/").rstrip("/")
    if music_dir in normalized:
        rel = normalized[len(music_dir) + 1:]
        parts = rel.split("/")
        if len(parts) >= 2:
            return parts[0]
    # Fallback: return parent directory name
    import os
    return os.path.basename(os.path.dirname(filepath))


def _natural_sort_key(s):
    """Sort key that extracts trailing numbers for natural ordering (song 2 before song 10)."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def get_subfolders():
    """Get naturally sorted list of unique subfolders that have songs."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT subfolder FROM songs WHERE subfolder IS NOT NULL AND subfolder != ''"
        ).fetchall()
        subfolders = [r["subfolder"] for r in rows]
        subfolders.sort(key=_natural_sort_key)
        return subfolders
    finally:
        conn.close()


def get_subfolder_song_counts():
    """Get count of songs per subfolder, naturally sorted."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT subfolder, COUNT(*) as cnt FROM songs WHERE subfolder IS NOT NULL AND subfolder != '' GROUP BY subfolder"
        ).fetchall()
        result = {r["subfolder"]: r["cnt"] for r in rows}
        return dict(sorted(result.items(), key=lambda kv: _natural_sort_key(kv[0])))
    finally:
        conn.close()


def get_song_ids_in_subfolders(folders):
    """Get all song IDs that belong to the given subfolders."""
    if not folders:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in folders)
        rows = conn.execute(
            f"SELECT id FROM songs WHERE subfolder IN ({placeholders})",
            list(folders),
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def _row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def add_song(filepath, file_hash, file_mtime=None):
    conn = get_connection()
    try:
        if file_mtime is not None:
            conn.execute(
                "INSERT OR IGNORE INTO songs (filepath, file_hash, file_mtime) VALUES (?, ?, ?)",
                (filepath, file_hash, file_mtime),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO songs (filepath, file_hash) VALUES (?, ?)",
                (filepath, file_hash),
            )
        conn.commit()
    finally:
        conn.close()


_ALLOWED_SONG_COLUMNS = {
    "filepath", "file_hash",
    "artist", "title", "album", "user_tags", "user_notes",
    "energy", "valence", "bpm", "ingest_phase", "art_file",
    "play_count", "skip_count", "complete_count", "replay_count",
    "avg_completion", "recovery_score", "last_played",
    "ai_description", "emotional_tags", "genre_tags", "instrument_tags",
    "lyrics", "keyscale", "timesignature", "vocal_language",
    "raw_rms", "crest_factor", "spectral_contrast",
    "spectral_rolloff", "spectral_flatness",
    "duration_secs", "rms_envelope", "dj_effect_eligible",
    "subfolder", "cluster_id", "gain_db", "file_mtime",
}


def update_song(song_id, **kwargs):
    conn = get_connection()
    try:
        if not kwargs:
            return
        allowed = {k: v for k, v in kwargs.items() if k in _ALLOWED_SONG_COLUMNS}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        values = list(allowed.values()) + [song_id]
        conn.execute(f"UPDATE songs SET {sets} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def get_song(song_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_songs_by_ids(song_ids):
    """Batch-fetch songs by ID list. Returns dict keyed by song_id."""
    if not song_ids:
        return {}
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(song_ids))
        rows = conn.execute(
            f"SELECT * FROM songs WHERE id IN ({placeholders})", song_ids
        ).fetchall()
        return {r["id"]: _row_to_dict(r) for r in rows}
    finally:
        conn.close()


def get_song_by_filepath(filepath):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM songs WHERE filepath = ?", (filepath,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_all_song_ids():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id FROM songs ORDER BY id").fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def get_unprocessed():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM songs WHERE ingest_phase != 'clustered' ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_all_filepaths():
    """Return list of (id, filepath) for all songs."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, filepath FROM songs ORDER BY id").fetchall()
        return [(r["id"], r["filepath"]) for r in rows]
    finally:
        conn.close()


def delete_song(song_id):
    """Delete a song by ID. Returns the deleted song dict, or None."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        if not row:
            return None
        deleted = dict(row)
        _delete_child_rows(conn, [song_id])
        conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        conn.commit()
        return deleted
    finally:
        conn.close()


def delete_songs_batch(song_ids):
    """Delete multiple songs by ID list. Returns list of deleted song dicts."""
    if not song_ids:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(song_ids))
        rows = conn.execute(
            f"SELECT * FROM songs WHERE id IN ({placeholders})", song_ids
        ).fetchall()
        deleted = [dict(r) for r in rows]
        _delete_child_rows(conn, song_ids)
        conn.execute(f"DELETE FROM songs WHERE id IN ({placeholders})", song_ids)
        conn.commit()
        return deleted
    finally:
        conn.close()


def _delete_child_rows(conn, song_ids):
    """Delete rows from child tables that reference songs via FK."""
    if not song_ids:
        return
    ph = ",".join("?" * len(song_ids))
    conn.execute(f"DELETE FROM play_events WHERE song_id IN ({ph})", song_ids)
    conn.execute(f"DELETE FROM transitions WHERE from_song_id IN ({ph}) OR to_song_id IN ({ph})", song_ids + song_ids)
    conn.execute(f"DELETE FROM queue_history WHERE song_id IN ({ph})", song_ids)
    conn.execute(f"DELETE FROM blacklist WHERE song_id IN ({ph})", song_ids)
    conn.execute(f"DELETE FROM collection_songs WHERE song_id IN ({ph})", song_ids)
    conn.execute(f"DELETE FROM mood_skip_repulsion WHERE song_id IN ({ph})", song_ids)


def set_ingest_error(song_id, error_msg):
    """Record an ingest error for a song without advancing its phase."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE songs SET ingest_error = ?, ingest_error_count = COALESCE(ingest_error_count, 0) + 1 WHERE id = ?",
            (error_msg[:500], song_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_ingest_failures():
    """Get songs that failed during ingest."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM songs
               WHERE (ingest_error IS NOT NULL AND ingest_error != '')
                OR (ingest_phase LIKE '%failed')
               ORDER BY id"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_by_ingest_phase(phase):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM songs WHERE ingest_phase = ? ORDER BY id", (phase,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_ingest_phase(song_id, phase):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE songs SET ingest_phase = ? WHERE id = ?", (phase, song_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_ingest_phases(song_ids, phase):
    if not song_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join(["?"] * len(song_ids))
        conn.execute(
            f"UPDATE songs SET ingest_phase = ? WHERE id IN ({placeholders})",
            [phase] + song_ids,
        )
        conn.commit()
    finally:
        conn.close()


def get_ingest_status():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ingest_phase, COUNT(*) as cnt FROM songs GROUP BY ingest_phase ORDER BY ingest_phase"
        ).fetchall()
        return {r["ingest_phase"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def get_total_song_count():
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM songs").fetchone()
        return row["cnt"]
    finally:
        conn.close()


def record_play_event(song_id, session_id, signal, **kwargs):
    conn = get_connection()
    try:
        now = datetime.now()
        hour = now.hour
        day_type = "weekday" if now.weekday() < 5 else "weekend"
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO play_events 
               (song_id, session_id, signal, skip_at_secs, completion_pct, 
                queue_slot, mood_state, hour_of_day, day_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                song_id,
                session_id,
                signal,
                kwargs.get("skip_at_secs"),
                kwargs.get("completion_pct", 0.0),
                kwargs.get("queue_slot"),
                kwargs.get("mood_state"),
                hour,
                day_type,
            ),
        )
        # Update song stats based on signal type
        if signal == "completed":
            conn.execute(
                "UPDATE songs SET complete_count = complete_count + 1 WHERE id = ?",
                (song_id,),
            )
        elif signal in ("skip_early", "skip_late", "thumbed_down"):
            conn.execute(
                "UPDATE songs SET skip_count = skip_count + 1 WHERE id = ?",
                (song_id,),
            )
        elif signal == "playing":
            conn.execute(
                "UPDATE songs SET play_count = play_count + 1, last_played = ? WHERE id = ?",
                (now.isoformat(), song_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_signal_since_last_play(song_id, signal_type):
    """Check if there's a non-undone event of signal_type for song_id
    that occurred after the most recent 'playing' event for that song."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM play_events
               WHERE song_id = ? AND signal = ? AND (undone IS NULL OR undone = 0)
               AND played_at > COALESCE(
                   (SELECT MAX(played_at) FROM play_events
                    WHERE song_id = ? AND signal = 'playing'),
                   '1970-01-01'
               )""",
            (song_id, signal_type, song_id),
        ).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def record_skip_feedback(session_id, skip_streak, reason, duration=None):
    """Record a skip feedback reason."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO skip_feedback (session_id, skip_streak, reason, duration) VALUES (?, ?, ?, ?)",
            (session_id, skip_streak, reason, duration),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_skip_feedback(session_id, limit=5):
    """Get recent skip feedback for a session."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM skip_feedback WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def record_mood_skip_repulsion(mood_state, reason, song_id, audio_embedding):
    """Store a song's audio embedding as mood-specific skip repulsion."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO mood_skip_repulsion (mood_state, reason, song_id, audio_embedding) VALUES (?, ?, ?, ?)",
            (mood_state, reason, song_id, json.dumps(audio_embedding)),
        )
        conn.commit()
    finally:
        conn.close()


def get_mood_repulsion_vector(mood_state, reason=None, limit=20):
    """Get time-weighted average audio embedding of rejected songs in this mood.

    Recent entries have full weight; older entries decay exponentially.
    Returns a 512-dim vector (list of floats) or None if insufficient data.
    """
    conn = get_connection()
    try:
        if reason:
            rows = conn.execute(
                "SELECT audio_embedding, created_at FROM mood_skip_repulsion WHERE mood_state = ? AND reason = ? ORDER BY created_at DESC LIMIT ?",
                (mood_state, reason, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT audio_embedding, created_at FROM mood_skip_repulsion WHERE mood_state = ? ORDER BY created_at DESC LIMIT ?",
                (mood_state, limit),
            ).fetchall()
        if not rows:
            return None

        now = datetime.now()
        weighted_sum = None
        weight_total = 0.0
        for row in rows:
            emb = json.loads(row[0])
            created = datetime.fromisoformat(row[1]) if row[1] else now
            age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
            # Exponential decay: full weight at 0h, ~0.5 at 24h, ~0.1 at 72h
            w = math.exp(-0.029 * age_hours)
            if weighted_sum is None:
                weighted_sum = [v * w for v in emb]
            else:
                weighted_sum = [a + b * w for a, b in zip(weighted_sum, emb)]
            weight_total += w

        if weight_total <= 0:
            return None
        return [v / weight_total for v in weighted_sum]
    finally:
        conn.close()


def cleanup_old_mood_repulsion(days=30):
    """Delete mood_skip_repulsion entries older than N days."""
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn.execute("DELETE FROM mood_skip_repulsion WHERE created_at < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def get_completed_songs_by_mood(mood_state, limit=50):
    """Get song IDs completed in a given mood state, most recent first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT DISTINCT song_id FROM play_events
               WHERE mood_state = ? AND signal = 'completed'
               ORDER BY played_at DESC LIMIT ?""",
            (mood_state, limit),
        ).fetchall()
        return [{"song_id": r["song_id"]} for r in rows]
    finally:
        conn.close()


def undo_play_event(song_id, signal_type):
    """Mark the most recent play_event as undone and reverse side effects."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE play_events SET undone = 1
               WHERE id = (SELECT id FROM play_events
                           WHERE song_id = ? AND signal = ? AND (undone IS NULL OR undone = 0)
                           ORDER BY played_at DESC LIMIT 1)""",
            (song_id, signal_type),
        )
        # Reverse the counter on the songs table
        if signal_type in ("skip_early", "skip_late"):
            conn.execute(
                "UPDATE songs SET skip_count = MAX(0, skip_count - 1) WHERE id = ?",
                (song_id,),
            )
        elif signal_type == "completed":
            conn.execute(
                "UPDATE songs SET complete_count = MAX(0, complete_count - 1) WHERE id = ?",
                (song_id,),
            )
        conn.commit()
    finally:
        conn.close()


def get_last_play_event(song_id, signal_type):
    """Get the most recent play_event for a song/signal combo."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM play_events WHERE song_id = ? AND signal = ? ORDER BY played_at DESC LIMIT 1",
            (song_id, signal_type),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def search_songs_advanced(query=None, artist=None, genre_tag=None,
                           energy_min=None, energy_max=None,
                           valence_min=None, valence_max=None,
                           cluster_id=None, limit=20, offset=0,
                           sort_by=None, order="ASC", fuzzy=True):
    """Advanced song search with multiple optional filters.

    All filters are ANDed together. Only returns clustered songs.
    When fuzzy=True, applies rapidfuzz token_sort_ratio ranking to handle
    spelling errors (e.g. "Radiohed" -> "Radiohead"). Merges SQL LIKE
    results with fuzzy-ranked results and deduplicates by song ID.
    """
    conn = get_connection()
    try:
        conditions = ["s.ingest_phase = 'clustered'"]
        params = []

        if query:
            conditions.append("(LOWER(s.title) LIKE ? OR LOWER(s.artist) LIKE ?)")
            like = f"%{query.lower()}%"
            params.extend([like, like])

        if artist:
            conditions.append("LOWER(s.artist) LIKE ?")
            params.append(f"%{artist.lower()}%")

        if genre_tag:
            conditions.append("LOWER(s.genre_tags) LIKE ?")
            params.append(f"%{genre_tag.lower()}%")

        if energy_min is not None:
            conditions.append("s.energy >= ?")
            params.append(float(energy_min))

        if energy_max is not None:
            conditions.append("s.energy <= ?")
            params.append(float(energy_max))

        if valence_min is not None:
            conditions.append("s.valence >= ?")
            params.append(float(valence_min))

        if valence_max is not None:
            conditions.append("s.valence <= ?")
            params.append(float(valence_max))

        if cluster_id is not None:
            conditions.append("s.cluster_id = ?")
            params.append(int(cluster_id))

        allowed_sorts = {"energy", "valence", "bpm", "title", "artist",
                         "play_count", "avg_completion", "duration_secs",
                         "added_at"}
        order_col = "s.id"
        if sort_by and sort_by in allowed_sorts:
            order_col = f"s.{sort_by}"
        order_dir = "ASC" if order.upper() != "DESC" else "DESC"

        where = " AND ".join(conditions)
        sql = f"""SELECT s.* FROM songs s
                  LEFT JOIN blacklist b ON s.id = b.song_id
                  WHERE {where} AND b.song_id IS NULL
                  ORDER BY {order_col} {order_dir}
                  LIMIT ? OFFSET ?"""
        params.extend([limit, offset])

        like_rows = conn.execute(sql, params).fetchall()
        like_results = [_row_to_dict(r) for r in like_rows]

        # Fuzzy ranking pass (runs when query/artist provided and fuzzy enabled)
        if fuzzy and (query or artist) and not genre_tag and energy_min is None and energy_max is None and valence_min is None and valence_max is None and cluster_id is None:
            # No restrictive filters — do full fuzzy scan
            fuzzy_rows = conn.execute(
                "SELECT s.*, s.filepath FROM songs s LEFT JOIN blacklist b ON s.id = b.song_id WHERE s.ingest_phase = 'clustered' AND b.song_id IS NULL",
            ).fetchall()
            fuzzy_results = []
            for r in fuzzy_rows:
                d = dict(r)
                artist_text = (d.get("artist") or "").strip()
                title_text = (d.get("title") or "").strip()
                # Strip common filename prefixes like "01 - "
                title_text = re.sub(r'^\d+\s*-\s*', '', title_text).strip()
                if not artist_text and not title_text:
                    continue
                combined = f"{artist_text} {title_text}"
                score = 0
                # Compare query against artist, title, and combined — take max
                if query:
                    q = query.lower()
                    score = max(score, fuzz.token_sort_ratio(q, artist_text.lower()))
                    score = max(score, fuzz.token_sort_ratio(q, title_text.lower()))
                    score = max(score, fuzz.token_sort_ratio(q, combined.lower()))
                if artist:
                    a = artist.lower()
                    score = max(score, fuzz.token_sort_ratio(a, artist_text.lower()))
                    score = max(score, fuzz.token_sort_ratio(a, title_text.lower()))
                    score = max(score, fuzz.token_sort_ratio(a, combined.lower()))
                if score >= 65:
                    d["_fuzzy_score"] = score
                    d["_fuzzy"] = True
                    fuzzy_results.append(d)
            fuzzy_results.sort(key=lambda x: -x["_fuzzy_score"])
            # Merge: like_results first, then fuzzy, dedup by ID
            seen_ids = {r["id"] for r in like_results}
            for fr in fuzzy_results:
                if fr["id"] not in seen_ids:
                    like_results.append(fr)
                    seen_ids.add(fr["id"])
            # Trim to limit
            if offset > 0:
                like_results = like_results[offset:]
            like_results = like_results[:limit]
            return like_results

        return like_results
    finally:
        conn.close()


def get_recent_plays(song_id, n=10):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM play_events WHERE song_id = ? ORDER BY played_at DESC LIMIT ?",
            (song_id, n),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_neutral_centroid_songs(n=20):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM songs 
               WHERE ingest_phase = 'clustered' 
                AND energy BETWEEN 0.3 AND 0.7
                AND valence BETWEEN 0.3 AND 0.7
               ORDER BY ABS(energy - 0.5) + ABS(valence - 0.5)
               LIMIT ?""",
            (n,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_neutral_centroid_songs_excluding(n=20, exclude_ids=None):
    conn = get_connection()
    try:
        query = """SELECT * FROM songs 
                   WHERE ingest_phase = 'clustered' 
                    AND energy BETWEEN 0.3 AND 0.7
                    AND valence BETWEEN 0.3 AND 0.7"""
        params = []
        if exclude_ids:
            placeholders = ','.join('?' * len(exclude_ids))
            query += f" AND id NOT IN ({placeholders})"
            params.extend(exclude_ids)
        query += " ORDER BY ABS(energy - 0.5) + ABS(valence - 0.5) LIMIT ?"
        params.append(n)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_blacklisted():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT b.song_id, b.level, b.reason, b.mood, b.artist, b.cluster_ids, b.added_at, b.expires_at, s.artist as song_artist, s.title, s.cluster_id FROM blacklist b LEFT JOIN songs s ON b.song_id = s.id WHERE b.expires_at IS NULL OR b.expires_at > datetime('now')"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def blacklist_song(song_id, level="never_play", reason="user", mood=None, artist=None, cluster_ids=None, expires_at=None):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO blacklist (song_id, level, reason, mood, artist, cluster_ids, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (song_id, level, reason, mood, artist, cluster_ids, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def unblacklist_song(song_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM blacklist WHERE song_id = ?", (song_id,))
        conn.commit()
    finally:
        conn.close()


def get_songs_by_artists(artist_names):
    """Get all song IDs for a set of artist names (case-insensitive)."""
    if not artist_names:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in artist_names)
        rows = conn.execute(
            f"SELECT id FROM songs WHERE LOWER(artist) IN ({placeholders})",
            [a.lower() for a in artist_names],
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def get_songs_by_clusters(cluster_ids):
    """Get all song IDs for a set of cluster IDs."""
    if not cluster_ids:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in cluster_ids)
        rows = conn.execute(
            f"SELECT id FROM songs WHERE cluster_id IN ({placeholders})",
            list(cluster_ids),
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


# ── Collections ──────────────────────────────────────────────


def get_collections():
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM collection_songs WHERE collection_id = c.id) as song_count
               FROM collections c ORDER BY c.name"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def create_collection(name, description=""):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            (name, description),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_collection(collection_id, name=None, description=None):
    conn = get_connection()
    try:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if updates:
            updates["updated_at"] = datetime.now().isoformat()
            sets = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [collection_id]
            conn.execute(f"UPDATE collections SET {sets} WHERE id = ?", values)
            conn.commit()
    finally:
        conn.close()


def delete_collection(collection_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        conn.commit()
    finally:
        conn.close()


def get_collection_songs(collection_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.*, cs.position, cs.added_at as collection_added_at
               FROM collection_songs cs
               JOIN songs s ON cs.song_id = s.id
               WHERE cs.collection_id = ?
               ORDER BY cs.position""",
            (collection_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def add_to_collection(collection_id, song_id, position=None):
    conn = get_connection()
    try:
        if position is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM collection_songs WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            position = row["next_pos"]
        conn.execute(
            "INSERT OR IGNORE INTO collection_songs (collection_id, song_id, position) VALUES (?, ?, ?)",
            (collection_id, song_id, position),
        )
        conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?",
                      (datetime.now().isoformat(), collection_id))
        conn.commit()
    finally:
        conn.close()


def remove_from_collection(collection_id, song_id):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM collection_songs WHERE collection_id = ? AND song_id = ?",
            (collection_id, song_id),
        )
        conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?",
                      (datetime.now().isoformat(), collection_id))
        conn.commit()
    finally:
        conn.close()


def reorder_collection(collection_id, song_ids):
    """Reorder songs in a collection by position."""
    conn = get_connection()
    try:
        now = datetime.now()
        conn.execute("BEGIN")
        params = [(pos, collection_id, song_id) for pos, song_id in enumerate(song_ids)]
        conn.executemany(
            "UPDATE collection_songs SET position = ? WHERE collection_id = ? AND song_id = ?",
            params,
        )
        conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?",
                      (now.isoformat(), collection_id))
        conn.commit()
    finally:
        conn.close()


def get_collection_names_for_song(song_id):
    """Return comma-separated collection names that contain this song."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT c.name FROM collections c
               JOIN collection_songs cs ON cs.collection_id = c.id
               WHERE cs.song_id = ?
               ORDER BY c.name""",
            (song_id,),
        ).fetchall()
        return ", ".join(r["name"] for r in rows) if rows else ""
    finally:
        conn.close()


def get_setting(key):
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value) if value is not None else ""),
        )
        conn.commit()
        _invalidate_setting(key)
    finally:
        conn.close()


def get_all_settings():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def get_recently_played(song_id, hours=3):
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT * FROM play_events WHERE song_id = ? AND played_at > ? ORDER BY played_at DESC LIMIT 1",
            (song_id, cutoff),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_played_in_last_hours(song_id, hours=3):
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM play_events WHERE song_id = ? AND played_at > ?",
            (song_id, cutoff),
        ).fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


def get_song_by_cluster(cluster_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM songs WHERE cluster_id = ? AND ingest_phase = 'clustered' ORDER BY RANDOM() LIMIT 10",
            (cluster_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def create_session(initial_mood=None):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO sessions (started_at, initial_mood) VALUES (?, ?)",
            (datetime.now().isoformat(), initial_mood),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def close_session(session_id):
    """Close a session by setting ended_at."""
    if not session_id:
        return
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (datetime.now().isoformat(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(session_id):
    """Retrieve a session by ID."""
    if not session_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        session = _row_to_dict(row)
        if session.get("mood_changes"):
            session["mood_changes"] = json.loads(session["mood_changes"])
        return session
    finally:
        conn.close()


def record_mood_change(session_id, mood_state, source="auto"):
    """Record a mood change in the session's mood_changes JSON array."""
    if not session_id:
        return
    conn = get_connection()
    try:
        entry = {"mood": mood_state, "timestamp": datetime.now().isoformat(), "source": source}
        row = conn.execute("SELECT mood_changes FROM sessions WHERE id = ?", (session_id,)).fetchone()
        existing = json.loads(row["mood_changes"]) if row and row["mood_changes"] else []
        existing.append(entry)
        conn.execute(
            "UPDATE sessions SET mood_changes = ? WHERE id = ?",
            (json.dumps(existing), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_replay_count(song_id):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE songs SET replay_count = replay_count + 1, play_count = play_count + 1 WHERE id = ?",
            (song_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_queue_song_ids(limit=50):
    """Get the most recently queued song IDs for seeding the recommendation buffer."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT song_id FROM queue_history ORDER BY played_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["song_id"] for r in rows]
    finally:
        conn.close()


def get_recent_signals(signal_types=None, limit=2000, session_id=None):
    """Get recent play events for taste memory.

    Returns list of dicts with song_id, played_at, signal, session_id.
    """
    conn = get_connection()
    try:
        if signal_types:
            placeholders = ",".join("?" * len(signal_types))
            where_clauses = f"signal IN ({placeholders})"
            params = list(signal_types)
        else:
            where_clauses = "1=1"
            params = []

        if session_id is not None:
            where_clauses += " AND session_id = ?"
            params.append(session_id)

        where_clauses += " AND (undone IS NULL OR undone = 0)"

        query = f"SELECT song_id, played_at, signal, session_id FROM play_events WHERE {where_clauses} ORDER BY played_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_recovery_candidates(n=10):
    """Get songs with high recovery_score for fallback selection."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM songs
               WHERE recovery_score > 0.75
               AND ingest_phase = 'clustered'
               ORDER BY recovery_score DESC, play_count ASC
               LIMIT ?""",
            (n,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_top_completion(n=50):
    """Get top songs by avg_completion (fallback for recovery)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM songs
               WHERE avg_completion > 0
               AND ingest_phase = 'clustered'
               ORDER BY avg_completion DESC
               LIMIT ?""",
            (n,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def calculate_recovery_score(song_id):
    """Calculate recovery_score for a song.

    Formula: 0.7 * behavioral + 0.3 * prior
      behavioral = (complete / total) * log(play_count + 1)
      prior = (1 - energy) * valence  (calm + pleasant = good recovery)
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT complete_count, skip_count, play_count, energy, valence FROM songs WHERE id = ?",
            (song_id,),
        ).fetchone()
        if not row:
            return 0.0
        complete = row["complete_count"]
        skip = row["skip_count"]
        plays = row["play_count"]
        energy = row["energy"] or 0.5
        valence = row["valence"] or 0.5
        total = complete + skip
        if total == 0:
            behavioral = 0.5
        else:
            behavioral = (complete / total) * math.log(plays + 1)
        # Prior: calm (low energy) + pleasant (high valence) = good recovery
        prior = (1 - energy) * valence
        score = 0.7 * behavioral + 0.3 * prior
        score = min(1.0, score)
        conn.execute(
            "UPDATE songs SET recovery_score = ? WHERE id = ?",
            (score, song_id),
        )
        conn.commit()
        return score
    finally:
        conn.close()


def update_avg_completion(song_id, completion_pct):
    """Update avg_completion with rolling average."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT avg_completion, complete_count FROM songs WHERE id = ?",
            (song_id,),
        ).fetchone()
        if not row:
            return
        old_avg = row["avg_completion"] or 0.0
        complete_count = row["complete_count"] or 0
        if complete_count == 0:
            new_avg = completion_pct
        else:
            new_avg = (old_avg * complete_count + completion_pct) / (complete_count + 1)
        conn.execute(
            "UPDATE songs SET avg_completion = ? WHERE id = ?",
            (new_avg, song_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_hourly_completion_profile():
    """Get per-hour average completion_pct from play_events.

    Returns list of 24 floats (0.0-1.0), one per hour.
    Defaults to 0.5 for hours with no data.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT hour_of_day, AVG(completion_pct) as avg_comp
               FROM play_events
               WHERE signal IN ('completed', 'skip_early', 'skip_late')
                AND completion_pct IS NOT NULL
               GROUP BY hour_of_day
               ORDER BY hour_of_day"""
        ).fetchall()
        profile = [0.5] * 24
        for r in rows:
            h = r["hour_of_day"]
            if 0 <= h < 24:
                profile[h] = r["avg_comp"] / 100.0  # completion_pct is 0-100
        return profile
    finally:
        conn.close()


def get_cluster_staleness():
    """Get hours since last play per cluster.

    Returns dict {cluster_id: hours_since_last_play}.
    Clusters with no play history default to 24h (very stale).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.cluster_id,
                      (julianday('now') - julianday(MAX(pe.played_at))) * 24 AS hours_ago
               FROM songs s
               LEFT JOIN play_events pe ON s.id = pe.song_id
               WHERE s.ingest_phase = 'clustered' AND s.cluster_id >= 0
               GROUP BY s.cluster_id"""
        ).fetchall()
        result = {}
        for r in rows:
            result[r["cluster_id"]] = r["hours_ago"] if r["hours_ago"] is not None else 24.0
        return result
    finally:
        conn.close()


def get_archetype_metrics():
    """Get aggregated metrics for archetype detection."""
    conn = get_connection()
    try:
        # Total plays per signal type
        rows = conn.execute(
            """SELECT signal, COUNT(*) as cnt FROM play_events
               GROUP BY signal"""
        ).fetchall()
        signal_counts = {r["signal"]: r["cnt"] for r in rows}

        # Unique songs played
        unique_songs = conn.execute(
            "SELECT COUNT(DISTINCT song_id) as cnt FROM play_events"
        ).fetchone()["cnt"]

        # Total plays
        total_plays = conn.execute(
            "SELECT COUNT(*) as cnt FROM play_events"
        ).fetchone()["cnt"]

        # Completion rate
        completed = signal_counts.get("completed", 0)
        skip_early = signal_counts.get("skip_early", 0)
        skip_late = signal_counts.get("skip_late", 0)
        total_signals = total_plays

        return {
            "total_plays": total_plays,
            "unique_songs": unique_songs,
            "completed": completed,
            "skip_early": skip_early,
            "skip_late": skip_late,
            "completion_rate": completed / max(1, total_signals),
            "signal_counts": signal_counts,
        }
    finally:
        conn.close()


# ── Export/Import ─────────────────────────────────────────────


def get_collections_with_songs():
    """Return all collections with their song IDs."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM collection_songs WHERE collection_id = c.id) as song_count
               FROM collections c ORDER BY c.name"""
        ).fetchall()
        result = []
        for r in rows:
            col = _row_to_dict(r)
            songs = conn.execute(
                "SELECT song_id, position FROM collection_songs WHERE collection_id = ? ORDER BY position",
                (col["id"],),
            ).fetchall()
            col["song_ids"] = [s["song_id"] for s in songs]
            result.append(col)
        return result
    finally:
        conn.close()


def get_collection_by_name(name):
    """Return a collection by name, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM collections WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_user_modified_songs():
    """Return songs with non-default user_tags, user_notes, or dj_effect_eligible."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, user_tags, user_notes, dj_effect_eligible
               FROM songs
               WHERE (user_tags IS NOT NULL AND user_tags != '')
                  OR (user_notes IS NOT NULL AND user_notes != '')
                  OR dj_effect_eligible != 1"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── Song Suppression (temporal skip feedback) ────────────────


def add_song_suppression(song_id, reason, hours=None, session_id=None):
    """Add a temporal suppression for a song.
    
    If hours is set, expires_at = now + hours.
    If session_id is set, expires only when session ends.
    """
    conn = get_connection()
    try:
        if hours is not None:
            from datetime import datetime, timedelta
            expires = (datetime.now() + timedelta(hours=hours)).isoformat()
            conn.execute(
                "INSERT INTO song_suppression (song_id, reason, expires_at) VALUES (?, ?, ?)",
                (song_id, reason, expires),
            )
        elif session_id is not None:
            conn.execute(
                "INSERT INTO song_suppression (song_id, reason, session_id) VALUES (?, ?, ?)",
                (song_id, reason, session_id),
            )
        else:
            conn.execute(
                "INSERT INTO song_suppression (song_id, reason) VALUES (?, ?)",
                (song_id, reason),
            )
        conn.commit()
    finally:
        conn.close()


def get_suppressed_song_ids(session_id=None):
    """Return set of song_id that are currently suppressed (not expired)."""
    conn = get_connection()
    try:
        query = """
            SELECT song_id FROM song_suppression
            WHERE (expires_at IS NULL OR expires_at > datetime('now'))
        """
        params = []
        if session_id is not None:
            query += " AND (session_id IS NULL OR session_id = ?)"
            params.append(session_id)
        rows = conn.execute(query, params).fetchall()
        return {r["song_id"] for r in rows}
    finally:
        conn.close()


def cleanup_expired_suppressions():
    """Remove song suppressions that have expired."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM song_suppression WHERE expires_at IS NOT NULL AND expires_at < datetime('now')")
        conn.commit()
    finally:
        conn.close()
