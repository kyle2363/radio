"""Generate embeddings for songs and sync to ChromaDB.

Supports two embedding types:
  - Text embeddings (qwen3-embedding via LLM provider) — metadata/caption/lyrics
  - Audio embeddings (MuQ-MuLan) — raw audio waveform similarity
"""

import json
import os
import logging
import concurrent.futures
from threading import Lock

# Suppress ChromaDB telemetry noise
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"
logging.getLogger("chromadb").setLevel(logging.ERROR)
try:
    import posthog
    posthog.disabled = True
    posthog.capture = lambda *a, **k: None
except ImportError:
    pass

import chromadb

import config
from llm import llm_embed
from db import database

logger = logging.getLogger(__name__)

_chroma_client = None


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        os.makedirs(config.CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return _chroma_client


def get_audio_collection():
    """Get or create the ChromaDB collection for MuQ-MuLan audio embeddings."""
    client = _get_chroma_client()
    return client.get_or_create_collection(
        config.MUQ_AUDIO_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def _build_embedding_text(song: dict, song_id: int = None) -> str:
    """Build a text representation of a song for embedding.

    Includes folder, collection, and neighboring folder for richer
    clustering. Artist appears once at the start (mean pooling makes
    duplication unnecessary).
    """
    parts = []
    if song.get("artist"):
        parts.append(f"artist: {song['artist']}")
    if song.get("title"):
        parts.append(f"title: {song['title']}")

    # Folder context: "song 41" and neighbors "song 40, song 42"
    if config.EMBED_INCLUDE_FOLDER and song.get("subfolder"):
        parts.append(f"folder: {song['subfolder']}")
        # Neighboring folders (+1 and -1)
        import re
        match = re.search(r'song\s+(\d+)', song["subfolder"])
        if match:
            num = int(match.group(1))
            neighbors = []
            if num > 31:
                neighbors.append(f"song {num - 1}")
            neighbors.append(f"song {num + 1}")
            parts.append(f"neighboring folder: {', '.join(neighbors)}")

    # Collection context: which user-curated collections contain this song
    if config.EMBED_INCLUDE_COLLECTIONS and song_id:
        collections = database.get_collection_names_for_song(song_id)
        if collections:
            parts.append(f"collection: {collections}")

    if song.get("genre_tags"):
        try:
            parts.append(f"genres: {', '.join(json.loads(song['genre_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("emotional_tags"):
        try:
            parts.append(f"mood: {', '.join(json.loads(song['emotional_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("instrument_tags"):
        try:
            parts.append(f"instruments: {', '.join(json.loads(song['instrument_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("ai_description"):
        parts.append(f"description: {song['ai_description'][:config.AI_DESCRIPTION_MAX_CHARS]}")
    if song.get("lyrics"):
        words = song["lyrics"].split()[:config.LYRICS_EMBED_WORDS]
        parts.append(f"lyrics: {' '.join(words)}")
    if song.get("energy") is not None:
        parts.append(f"energy: {song['energy']}")
    if song.get("valence") is not None:
        parts.append(f"valence: {song['valence']}")
    if song.get("bpm"):
        parts.append(f"bpm: {song['bpm']}")

    return " | ".join(parts) if parts else "unknown song"


def embed_song(song_id: int, song: dict) -> bool:
    """Embed a single song and upsert to ChromaDB."""
    try:
        embedding_text = _build_embedding_text(song, song_id=song_id)
        result = llm_embed().embed(
            model=None,
            input=embedding_text,
        )
        embeddings = result.get("embeddings", [])
        if not embeddings:
            raise ValueError("No embeddings returned from LLM")
        embedding = embeddings[0]

        chroma_client = _get_chroma_client()
        collection = chroma_client.get_or_create_collection(
            "songs",
            metadata={"hnsw:space": "cosine"},
        )

        # Delete any existing entry first to prevent ChromaDB duplicate ID issues
        try:
            collection.delete(ids=[str(song_id)])
        except Exception:
            pass

        collection.upsert(
            ids=[str(song_id)],
            embeddings=[embedding],
            metadatas=[{
                "song_id": song_id,
                "artist": song.get("artist") or "",
                "title": song.get("title") or "",
                "energy": song.get("energy") if song.get("energy") is not None else 0.5,
                "valence": song.get("valence") if song.get("valence") is not None else 0.5,
                "cluster_id": song.get("cluster_id") if song.get("cluster_id") is not None else -1,
                "subfolder": song.get("subfolder") or "",
            }],
        )
        return True
    except Exception as e:
        print(f"  Failed to embed song {song_id}: {e}")
        return False


def embed_described_songs(song_ids: list) -> list:
    """Embed all described songs that aren't yet embedded. Returns list of embedded IDs."""
    chroma_client = _get_chroma_client()
    collection = chroma_client.get_or_create_collection(
        "songs",
        metadata={"hnsw:space": "cosine"},
    )

    # Check which songs are already in ChromaDB
    embedded_in_chroma = set()
    try:
        existing = collection.get(ids=[str(sid) for sid in song_ids])
        if existing:
            seen = set()
            for eid in existing["ids"]:
                if eid not in seen:
                    seen.add(eid)
                    embedded_in_chroma.add(eid)
    except Exception as e:
        print(f"  Warning: Could not check ChromaDB for existing songs: {e}")

    to_embed = [sid for sid in song_ids if str(sid) not in embedded_in_chroma]
    embedded_ids = []
    embed_lock = Lock()

    def _embed_one(song_id):
        song = database.get_song(song_id)
        if song and embed_song(song_id, song):
            database.set_ingest_phase(song_id, "embedded")
            with embed_lock:
                embedded_ids.append(song_id)
            return True
        print(f"  Failed to embed song {song_id}, will retry on next run")
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(_embed_one, sid): sid for sid in to_embed}
        for future in concurrent.futures.as_completed(futures):
            pass

    return embedded_ids


# ──────────────────────────────────────────────────────────
# Audio embeddings (MuQ-MuLan)
# ──────────────────────────────────────────────────────────

def embed_audio_song(song_id: int, filepath: str, song: dict = None) -> bool:
    """Embed a song's audio using MuQ-MuLan and upsert to audio collection.

    Args:
        song_id: database ID of the song
        filepath: path to the audio file
        song: optional song dict (avoids extra DB fetch if already available)

    Returns:
        True on success, False on failure
    """
    try:
        from llm.muq_engine import embed_audio

        embedding = embed_audio(filepath)

        audio_collection = get_audio_collection()

        # Delete first to avoid ChromaDB duplicate ID issues
        try:
            audio_collection.delete(ids=[str(song_id)])
        except Exception:
            pass

        # Use provided song dict or fetch from SQLite
        if song is None:
            song = database.get_song(song_id)
        audio_collection.upsert(
            ids=[str(song_id)],
            embeddings=[embedding],
            metadatas=[{
                "song_id": song_id,
                "artist": (song.get("artist") or "") if song else "",
                "title": (song.get("title") or "") if song else "",
                "energy": song.get("energy") if song and song.get("energy") is not None else 0.5,
                "valence": song.get("valence") if song and song.get("valence") is not None else 0.5,
                "cluster_id": song.get("cluster_id") if song and song.get("cluster_id") is not None else -1,
                "subfolder": song.get("subfolder") or "" if song else "",
            }],
        )
        return True
    except Exception as e:
        logger.warning("Failed to audio-embed song %d: %s", song_id, e)
        return False


def embed_audio_songs(song_ids: list) -> list:
    """Embed audio for multiple songs. Returns list of successfully embedded IDs."""
    audio_collection = get_audio_collection()

    # Check which songs already have audio embeddings
    already_embedded = set()
    try:
        existing = audio_collection.get(
            ids=[str(sid) for sid in song_ids],
            include=[],
        )
        if existing and existing["ids"]:
            already_embedded = set(existing["ids"])
    except Exception as e:
        logger.warning("Could not check audio collection for existing songs: %s", e)

    to_embed = [sid for sid in song_ids if str(sid) not in already_embedded]
    embedded_ids = []

    for song_id in to_embed:
        song = database.get_song(song_id)
        if song and song.get("filepath"):
            if embed_audio_song(song_id, song["filepath"], song=song):
                embedded_ids.append(song_id)
            else:
                logger.warning("Failed to audio-embed song %d, will retry", song_id)
        else:
            logger.warning("Song %d not found or no filepath, skipping audio embed", song_id)

    return embedded_ids
