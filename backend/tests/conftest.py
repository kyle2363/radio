"""Test fixtures and configuration."""

import os
import sys
import tempfile

# Ensure the backend directory is on sys.path for imports
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Disable telemetry before any chromadb imports
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"

import pytest


@pytest.fixture(scope="session")
def test_db_path():
    """Create a temporary SQLite database for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def patch_config(test_db_path, monkeypatch):
    """Override config paths for testing so nothing touches the real DB/Chroma."""
    import config as cfg

    monkeypatch.setattr(cfg, "DB_PATH", test_db_path)
    monkeypatch.setattr(cfg, "CHROMA_PATH", tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "MUQ_ENABLED", False)
    monkeypatch.setattr(cfg, "TTS_ENABLED", False)
    monkeypatch.setattr(cfg, "VLC_SUPPRESS_STDERR", True)

    # Initialize the test database schema
    from db import database
    database.init_db()

    yield
