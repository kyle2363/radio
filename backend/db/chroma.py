"""Shared ChromaDB client singleton.

All modules should import get_chroma_client() or get_chroma_collection() from here
instead of creating their own PersistentClient instances.
"""

import logging
import os
import threading

import config

logger = logging.getLogger(__name__)

_chroma_client = None
_chroma_lock = threading.Lock()


def get_chroma_client():
    """Get or create the shared ChromaDB PersistentClient singleton."""
    global _chroma_client
    if _chroma_client is None:
        with _chroma_lock:
            if _chroma_client is not None:
                return _chroma_client
            os.environ["CHROMA_NO_ANALYTICS"] = "1"
            os.environ["CHROMA_TELEMETRY_OFF"] = "1"
            import chromadb
            os.makedirs(config.CHROMA_PATH, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
            logger.debug("Created shared ChromaDB client at %s", config.CHROMA_PATH)
    return _chroma_client


def release_chroma_client():
    """Stop Chroma and forget cached clients so its folder can be moved.

    Every PersistentClient for one path shares a single running system that
    keeps the store's files open; on Windows that blocks renaming or deleting
    the folder. Returns True when a running system was stopped. The next
    get_chroma_client() call starts a fresh one.
    """
    import sys

    global _chroma_client
    with _chroma_lock:
        _chroma_client = None
    embedder = sys.modules.get("ingest.embedder")
    if embedder is not None:
        embedder._chroma_client = None
    if "chromadb" not in sys.modules:
        return False  # never started; do not load the library just to stop it
    from chromadb.api.shared_system_client import SharedSystemClient

    systems = list(SharedSystemClient._identifier_to_system.values())
    for system in systems:
        try:
            system.stop()
        except Exception as error:
            logger.warning("Could not stop a ChromaDB system: %s", error)
    SharedSystemClient.clear_system_cache()
    return bool(systems)


def get_chroma_collection(name="songs"):
    """Get or create a ChromaDB collection by name."""
    client = get_chroma_client()
    return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
