"""Isolate storage BEFORE test-module imports capture configuration paths."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".test-runtime"
RUNTIME.mkdir(exist_ok=True)
tempfile.tempdir = str(RUNTIME)
sys.path.insert(0, str(ROOT / "backend"))
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"

import pytest
import config as cfg

COLLECTION_ROOT = Path(tempfile.mkdtemp(prefix="collection-", dir=RUNTIME))
cfg.DATA_DIR = str(COLLECTION_ROOT)
cfg.MUSIC_DIR = str(COLLECTION_ROOT / "audio")
cfg.ACESTEP_JSON_DIR = str(COLLECTION_ROOT / "metadata")
cfg.DB_PATH = str(COLLECTION_ROOT / "radio.db")
for name, suffix in {
    "CHROMA_PATH": "chroma", "ART_DIR": "art", "TTS_CACHE_DIR": "tts",
    "LYRICS_TIMING_DIR": "lyrics", "SEPARATOR_MODEL_DIR": "models",
    "SEPARATOR_OUTPUT_DIR": "separated", "PROC_MGR_LOG_DIR": "logs",
    "LOG_PATH": "playback.log", "TALK_DEBUG_LOG_PATH": "talk.log",
}.items():
    setattr(cfg, name, str(COLLECTION_ROOT / suffix))
cfg.MUQ_ENABLED = False
cfg.TTS_ENABLED = False
cfg.PROC_MGR_ENABLED = False
cfg.VLC_SUPPRESS_STDERR = True


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    """A fresh DB per test; patch both config and captured database alias."""
    from db import database
    path = Path(tempfile.mkdtemp(prefix="case-", dir=RUNTIME)) / "radio.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", str(path))
    with database._settings_cache_lock:
        database._settings_cache.clear()
    database.init_db()
    with database.get_connection_cm() as conn:
        conn.executemany(
            "INSERT INTO songs(id, filepath, file_hash, title, energy, cluster_id, ingest_phase) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(i, str(COLLECTION_ROOT / "audio" / f"{i}.wav"), f"fixture-{i}", f"Fixture {i}",
              0.5 if i < 3 else 0.8, i - 1, "clustered")
             for i in range(1, 29)],
        )
        conn.commit()
    yield
    with database._settings_cache_lock:
        database._settings_cache.clear()
