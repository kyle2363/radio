from pathlib import Path
import sqlite3
import sys
import pytest

import config
from db import database


def test_database_connection_uses_disposable_database():
    root = Path(__file__).resolve().parents[2] / ".test-runtime"
    assert Path(database.DB_PATH).is_relative_to(root)
    assert database.DB_PATH == config.DB_PATH
    with database.get_connection_cm() as conn:
        actual = conn.execute("PRAGMA database_list").fetchone()[2]
        assert Path(actual).resolve() == Path(database.DB_PATH).resolve()
        assert conn.execute("SELECT count(*) FROM songs").fetchone()[0] == 28


def test_generated_media_paths_are_disposable():
    root = Path(__file__).resolve().parents[2] / ".test-runtime"
    for key in ("DATA_DIR", "MUSIC_DIR", "CHROMA_PATH", "ACESTEP_JSON_DIR",
                "TTS_CACHE_DIR", "SEPARATOR_OUTPUT_DIR", "LOG_PATH"):
        assert Path(getattr(config, key)).is_relative_to(root), key


def test_runner_rejects_production_database_connections():
    assert getattr(sys, "radio_test_isolated", False), "Run tests through scripts/run_tests.py"
    production = Path(__file__).resolve().parents[2] / "data" / "radio.db"
    with pytest.raises(PermissionError, match="SQLite"):
        sqlite3.connect(str(production))


def test_runner_rejects_original_audio_writes():
    production = Path(__file__).resolve().parents[2] / "audio_files" / "test-must-not-exist.wav"
    with pytest.raises(PermissionError, match="cannot write"):
        production.write_bytes(b"must never be written")
