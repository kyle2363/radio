"""Transition scoring for song-to-song flow.

Tracks success/failure of transitions between song pairs
and uses rolling average for flow scoring.
"""

from db import database


def record_transition(from_song_id, to_song_id, signal):
    """Record a transition between two songs.

    Args:
        from_song_id: the song that just ended
        to_song_id: the next song that started
        signal: "completed", "skip_early", "skip_late", "replayed", "hearted"
    """
    success_value = _signal_to_success(signal)

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM transitions WHERE from_song_id = ? AND to_song_id = ?",
            (from_song_id, to_song_id),
        ).fetchone()

        if row:
            existing_score = row["score"]
            existing_success = row["success_count"]
            existing_failure = row["failure_count"]
            new_score = 0.8 * existing_score + 0.2 * success_value
            conn.execute(
                "UPDATE transitions SET score = ?, success_count = ?, failure_count = ? WHERE from_song_id = ? AND to_song_id = ?",
                (new_score, existing_success + (1 if success_value > 0.5 else 0), existing_failure + (1 if success_value <= 0.5 else 0), from_song_id, to_song_id),
            )
        else:
            conn.execute(
                "INSERT INTO transitions (from_song_id, to_song_id, score, success_count, failure_count) VALUES (?, ?, ?, ?, ?)",
                (from_song_id, to_song_id, success_value, 1 if success_value > 0.5 else 0, 1 if success_value <= 0.5 else 0),
            )
        conn.commit()
    finally:
        conn.close()


def _signal_to_success(signal):
    """Map a signal to a success value (0.0 to 1.0)."""
    mapping = {
        "completed": 1.0,
        "hearted": 1.0,
        "replayed": 1.0,
        "skip_late": 0.5,
        "skip_early": 0.0,
    }
    return mapping.get(signal, 0.5)


def get_transition_score(from_song_id, to_song_id):
    """Get the transition score between two songs.

    Returns 0.5 (neutral) if no history exists.
    """
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT score FROM transitions WHERE from_song_id = ? AND to_song_id = ?",
            (from_song_id, to_song_id),
        ).fetchone()
        return row["score"] if row else 0.5
    finally:
        conn.close()


def get_transition_boost(from_song_id, candidate_song_id):
    """Get a small score boost for songs with good transition history."""
    score = get_transition_score(from_song_id, candidate_song_id)
    # Boost is small: 0.05 max
    if score > 0.7:
        return 0.05
    elif score > 0.6:
        return 0.02
    return 0.0


def get_transition_candidates(from_song_id, n=15):
    """Get songs with the best transition history from a given song.

    Returns list of dicts: {song_id, score} sorted by transition score descending.
    Only returns transitions with score > 0.5 (better than neutral).
    """
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT t.to_song_id AS song_id, t.score
               FROM transitions t
               WHERE t.from_song_id = ? AND t.score > 0.5
               ORDER BY t.score DESC
               LIMIT ?""",
            (from_song_id, n),
        ).fetchall()
        return [{"song_id": r["song_id"], "score": r["score"]} for r in rows]
    finally:
        conn.close()
