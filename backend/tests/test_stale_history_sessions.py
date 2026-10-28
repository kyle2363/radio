"""Startup history cleanup must leave resumable playback history intact."""

from db import database
import pytest


@pytest.fixture
def store():
    from db.playback_store import PlaybackStore
    return PlaybackStore()


def test_stale_cleanup_preserves_playback_history_but_closes_unrelated_sessions(store):
    resumable = store.create_session("client-a", "vlc")
    with database.get_connection_cm() as conn:
        unrelated = conn.execute(
            "INSERT INTO sessions(initial_mood) VALUES ('neutral')"
        ).lastrowid
        conn.commit()

    database.close_stale_sessions()

    with database.get_connection_cm() as conn:
        resumed_row = conn.execute(
            "SELECT ended_at FROM sessions WHERE id=?", (resumable["history_session_id"],)
        ).fetchone()
        unrelated_row = conn.execute(
            "SELECT ended_at FROM sessions WHERE id=?", (unrelated,)
        ).fetchone()
    assert resumed_row["ended_at"] is None
    assert unrelated_row["ended_at"] is not None


def test_stale_cleanup_works_before_playback_tables_exist():
    with database.get_connection_cm() as conn:
        session_id = conn.execute(
            "INSERT INTO sessions(initial_mood) VALUES ('neutral')"
        ).lastrowid
        conn.commit()

    database.close_stale_sessions()

    with database.get_connection_cm() as conn:
        row = conn.execute("SELECT ended_at FROM sessions WHERE id=?", (session_id,)).fetchone()
    assert row["ended_at"] is not None
