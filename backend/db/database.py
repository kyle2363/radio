import json
import math
import os
import sqlite3
from datetime import datetime, timedelta

import config

DB_PATH = config.DB_PATH


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


from contextlib import contextmanager


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
    # Migration: add raw feature columns if missing
    cursor = conn.execute("PRAGMA table_info(songs)")
    columns = {row[1] for row in cursor.fetchall()}
    for col in ["raw_rms", "crest_factor", "spectral_contrast", "spectral_rolloff", "spectral_flatness"]:
        if col not in columns:
            conn.execute(f"ALTER TABLE songs ADD COLUMN {col} REAL")
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
        conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Migration failed: {e}")
    finally:
        conn.close()


def _row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def add_song(filepath, file_hash):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO songs (filepath, file_hash) VALUES (?, ?)",
            (filepath, file_hash),
        )
        conn.commit()
    finally:
        conn.close()


def update_song(song_id, **kwargs):
    conn = get_connection()
    try:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [song_id]
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
        placeholders = ",".join("?" * len(song_ids))
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
            "SELECT ingest_phase, COUNT(*) as cnt FROM songs GROUP BY ingest_phase ORDER BY id"
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
        hour = datetime.now().hour
        day_type = "weekday" if datetime.now().weekday() < 5 else "weekend"
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
        elif signal in ("skip_early", "skip_late"):
            conn.execute(
                "UPDATE songs SET skip_count = skip_count + 1, play_count = play_count + 1 WHERE id = ?",
                (song_id,),
            )
        elif signal == "playing":
            conn.execute(
                "UPDATE songs SET play_count = play_count + 1, last_played = ? WHERE id = ?",
                (datetime.now().isoformat(), song_id),
            )
        conn.commit()
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


def update_song_stats(song_id, **kwargs):
    conn = get_connection()
    try:
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [song_id]
        conn.execute(f"UPDATE songs SET {sets} WHERE id = ?", values)
        conn.commit()
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


def get_blacklisted():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT b.song_id, b.reason, b.added_at, s.artist, s.title FROM blacklist b LEFT JOIN songs s ON b.song_id = s.id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def blacklist_song(song_id, reason):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO blacklist (song_id, reason) VALUES (?, ?)",
            (song_id, reason),
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
            (key, str(value)),
        )
        conn.commit()
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


def record_mood_change(session_id, mood_state, source="auto"):
    """Record a mood change in the session's mood_changes JSON array."""
    if not session_id:
        return
    conn = get_connection()
    try:
        row = conn.execute("SELECT mood_changes FROM sessions WHERE id = ?", (session_id,)).fetchone()
        changes = json.loads(row["mood_changes"]) if row and row["mood_changes"] else []
        changes.append({"mood": mood_state, "timestamp": datetime.now().isoformat(), "source": source})
        conn.execute(
            "UPDATE sessions SET mood_changes = ? WHERE id = ?",
            (json.dumps(changes), session_id),
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
