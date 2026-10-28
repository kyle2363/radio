import pytest

from db import database


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "radio.db"))
    database.init_db()
    database.add_song("synthetic.mp3", "synthetic-hash")
    conn = database.get_connection()
    try:
        song_id = conn.execute(
            "SELECT id FROM songs WHERE filepath = ?", ("synthetic.mp3",)
        ).fetchone()["id"]
    finally:
        conn.close()
    session_id = database.create_session("neutral")
    yield song_id, session_id


def test_insert_play_event_uses_callers_transaction(isolated_database):
    song_id, session_id = isolated_database
    conn = database.get_connection()
    try:
        conn.execute("BEGIN")
        database.insert_play_event(conn, song_id, session_id, "playing")

        assert conn.in_transaction
        assert conn.execute(
            "SELECT COUNT(*) FROM play_events WHERE song_id = ?", (song_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT play_count FROM songs WHERE id = ?", (song_id,)
        ).fetchone()[0] == 1

        conn.rollback()
    finally:
        conn.close()

    verify = database.get_connection()
    try:
        assert verify.execute(
            "SELECT COUNT(*) FROM play_events WHERE song_id = ?", (song_id,)
        ).fetchone()[0] == 0
        assert verify.execute(
            "SELECT play_count FROM songs WHERE id = ?", (song_id,)
        ).fetchone()[0] == 0
    finally:
        verify.close()


def test_record_play_event_wrapper_commits_legacy_counter_updates(isolated_database):
    song_id, session_id = isolated_database

    database.record_play_event(song_id, session_id, "playing")

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT play_count, last_played FROM songs WHERE id = ?", (song_id,)
        ).fetchone()
        assert row["play_count"] == 1
        assert row["last_played"]
        assert conn.execute(
            "SELECT COUNT(*) FROM play_events WHERE song_id = ? AND signal = 'playing'",
            (song_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()
