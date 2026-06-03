"""ChromaDB vector retrieval for song recommendations."""

import json
import logging
import math
import os
import random
from datetime import datetime, timedelta
import config
import chromadb
from db import database
from engine.utils import cosine_similarity

logger = logging.getLogger(__name__)

# ChromaDB singleton — avoids creating a new PersistentClient per call
_chroma_client = None


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        os.makedirs(config.CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return _chroma_client


def get_chroma_collection(name="songs"):
    """Get a ChromaDB collection by name."""
    client = _get_chroma_client()
    return client.get_collection(name)


def get_embedding_dimension() -> int:
    """Get the embedding dimension from the ChromaDB songs collection."""
    try:
        collection = get_chroma_collection()
        metadata = collection.metadata or {}
        if metadata.get("hnsw:M"):
            return int(metadata.get("hnsw:M"))
        if metadata.get("embedding_dimension"):
            return int(metadata.get("embedding_dimension"))
    except Exception:
        pass
    # Fallback: query one vector to discover dimension
    try:
        sample = get_chroma_collection().get(limit=1, include=["embeddings"])
        if sample and sample.get("embeddings") and sample["embeddings"][0]:
            return len(sample["embeddings"][0])
    except Exception:
        pass
    return 1024


def _jitter_vector(v, strength=0.03):
    """Add small random noise to a vector for variety between calls.
    
    This prevents the same candidates from being returned every time
    while preserving the overall direction of the query.
    """
    # logger.debug("_jitter_vector: v type=%s len=%s", type(v).__name__, len(v) if v else None)
    noise = [random.gauss(0, strength) for _ in v]
    noise_mag = math.sqrt(sum(x * x for x in noise))
    if noise_mag > 0:
        noise = [x / noise_mag * strength for x in noise]
    return [a + b for a, b in zip(v, noise)]


def search_by_vector(taste_vector, n=60, exclude_ids=None, min_energy=None, max_energy=None, min_valence=None, max_valence=None, subfolder_filter=None):
    """Search ChromaDB by embedding vector with optional filters.

    When subfolder_filter is set (list of folder names), only songs from
    those subfolders are returned. ChromaDB handles the filtering natively
    within the HNSW search, so every result is already restricted.

    Returns list of dicts with song_id, score, metadata.
    """
  # logger.debug("search_by_vector: taste_vector type=%s len=%s, n=%d, min_energy=%s, max_energy=%s, min_valence=%s, max_valence=%s",
   #              type(taste_vector).__name__, len(taste_vector) if taste_vector else None,
   #              n, min_energy, max_energy, min_valence, max_valence)
    if exclude_ids is None:
        exclude_ids = []

    collection = get_chroma_collection()
    # Add small jitter to taste vector for variety between calls
    query_vector = _jitter_vector(taste_vector)
    # Build where clause for folder filter
    where_clause = None
    if subfolder_filter:
        where_clause = {"subfolder": {"$in": subfolder_filter}}
    # Inflate query to get a broader pool, then subsample
    inflated_n = min(max(n * 2, 120), 200)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=inflated_n,
        where=where_clause,
        include=["distances", "metadatas"],
    )
 # logger.debug("search_by_vector: ChromaDB results ids=%r distances=%r metadatas_len=%s",
   #              results.get("ids")[:2] if results else None,
   #              results.get("distances")[:2] if results else None,
   #              len(results.get("metadatas", [])) if results else 0)

    song_ids = []
    ids_row = results["ids"][0] if results and results.get("ids") else []
    # logger.debug("search_by_vector: ids_row type=%s len=%s first_elem=%r", type(ids_row).__name__, len(ids_row) if ids_row else 0, ids_row[0] if ids_row else None)
    for i in range(len(ids_row)):
        song_id = int(ids_row[i])
        song_id = int(results["ids"][0][i])
        if song_id in exclude_ids:
            continue

        song_ids.append(song_id)

    # Fetch artist/title from SQLite since ChromaDB metadata may be stale/empty
    song_info = {}
    if song_ids:
        conn = database.get_connection()
        try:
            placeholders = ",".join("?" for _ in song_ids)
            rows = conn.execute(
                f"SELECT id, artist, title FROM songs WHERE id IN ({placeholders})",
                song_ids,
            ).fetchall()
            song_info = {r["id"]: {"artist": r["artist"] or "", "title": r["title"] or ""} for r in rows}
        finally:
            conn.close()

    candidates = []
    for i in range(len(ids_row)):
        song_id = int(ids_row[i])
        if song_id in exclude_ids:
            continue

        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        score = 1.0 - distance  # Convert distance to similarity

        # Apply energy/valence filters if specified
        energy = float(meta.get("energy", 0.5))
        valence = float(meta.get("valence", 0.5))

        if min_energy is not None and energy < min_energy:
            continue
        if max_energy is not None and energy > max_energy:
            continue
        if min_valence is not None and valence < min_valence:
            continue
        if max_valence is not None and valence > max_valence:
            continue

        info = song_info.get(song_id, {})
        candidates.append({
            "song_id": song_id,
            "score": score,
            "energy": energy,
            "valence": valence,
            "cluster_id": int(meta.get("cluster_id", -1)),
            "artist": info.get("artist", meta.get("artist", "")),
            "title": info.get("title", meta.get("title", "")),
        })

    # Apply cluster staleness bonus — boost songs from stale clusters
    staleness = database.get_cluster_staleness()
    # logger.debug("search_by_vector: staleness=%r (type=%s)", staleness, type(staleness).__name__)
    if staleness:
        for c in candidates:
            cid = c.get("cluster_id", -1)
            hours_ago = staleness.get(cid, 24.0)
            if hours_ago > 6 and cid >= 0:
                boost = min(0.08, hours_ago * 0.005)  # up to 0.08 bonus for very stale
                c["score"] = min(1.0, c.get("score", 0.5) + boost)

    # Shuffle within top tier to add variety while preserving quality
    if len(candidates) > n + 10:
        pool = candidates[:n + 10]
        random.shuffle(pool)
        candidates = pool[:n]
    else:
        candidates = candidates[:n]
    return candidates


def search_by_centroid(centroid_energy=0.5, centroid_valence=0.5, n=60):
    """Get songs near a neutral centroid (energy/valence based).

    Used for cold start when no taste vector exists.
    """
    collection = get_chroma_collection()
    results = collection.get(
        where={"$and": [
            {"energy": {"$gte": centroid_energy - 0.2}},
            {"energy": {"$lte": centroid_energy + 0.2}},
            {"valence": {"$gte": centroid_valence - 0.2}},
            {"valence": {"$lte": centroid_valence + 0.2}},
        ]},
        limit=min(n, 200),
        include=["metadatas", "ids"],
    )

    candidates = []
    for i in range(len(results["ids"])):
        meta = results["metadatas"][i]
        candidates.append({
            "song_id": int(meta.get("song_id", 0)),
            "score": 0.5,  # Neutral score for centroid songs
            "energy": float(meta.get("energy", 0.5)),
            "valence": float(meta.get("valence", 0.5)),
            "cluster_id": int(meta.get("cluster_id", -1)),
            "artist": meta.get("artist", ""),
            "title": meta.get("title", ""),
        })

    return candidates[:n]


def get_neutral_centroid_songs(n=60):
    """Get songs near the neutral centroid (energy~0.5, valence~0.5).

    Pre-computed at startup for cold start.
    """
    return database.get_neutral_centroid_songs(n)


def _apply_skip_feedback_bias(mood_energy_range, mood_valence_range, skip_feedback=None):
    """Adjust energy/valence ranges based on recent skip feedback.

    Args:
        mood_energy_range: (min, max) tuple or None
        mood_valence_range: (min, max) tuple or None
        skip_feedback: list of recent skip feedback dicts from DB

    Returns:
        Adjusted (mood_energy_range, mood_valence_range) tuple.
    """
   # logger.debug("_apply_skip_feedback_bias: mood_energy_range=%r (type=%s), mood_valence_range=%r (type=%s), skip_feedback=%r (type=%s)",
    #              mood_energy_range, type(mood_energy_range).__name__,
    #              mood_valence_range, type(mood_valence_range).__name__,
    #              skip_feedback, type(skip_feedback).__name__)
    if not skip_feedback:
        return mood_energy_range, mood_valence_range

    for fb in skip_feedback:
        reason = fb.get("reason", "")
        if reason == "too_intense" and mood_energy_range:
            lo, hi = mood_energy_range
            mood_energy_range = (lo, max(lo, hi - 0.15))
        elif reason == "too_slow" and mood_energy_range:
            lo, hi = mood_energy_range
            mood_energy_range = (min(hi, lo + 0.15), hi)
        elif reason == "too_samey":
            # No direct range adjustment — handled via jitter/exploration
            pass

    return mood_energy_range, mood_valence_range


def build_candidate_list(taste_vector, current_song_id, exclude_ids=None, mood_energy_range=None, mood_valence_range=None, repulsion_vector=None, skip_feedback=None, subfolder_filter=None):
    """Build a candidate list for slot allocation.

    Stage 1: ChromaDB vector search
    Stage 2: Exclude recently played, blacklist, current song
    Returns up to 40 candidates for LLM reranker.
    """
    if exclude_ids is None:
        exclude_ids = set()
    elif isinstance(exclude_ids, list):
        exclude_ids = set(exclude_ids)

    if current_song_id:
        exclude_ids.add(current_song_id)

    # Apply repulsion vector to taste vector (subtract disliked neighborhoods)
    if taste_vector and repulsion_vector:
        strength = 0.3
        taste_vector = [a - strength * b for a, b in zip(taste_vector, repulsion_vector)]

    # Get recently played song IDs (last 3 hours)
    recently_played = set()
    conn = database.get_connection()
    try:
        cutoff = (datetime.now() - timedelta(hours=3)).isoformat()
        rows = conn.execute(
            "SELECT DISTINCT song_id FROM play_events WHERE played_at > ?",
            (cutoff,),
        ).fetchall()
        recently_played = {r["song_id"] for r in rows}
    finally:
        conn.close()

    # Get blacklisted song IDs (smart levels)
    blacklisted = set()
    mood_blacklisted = set()
    cluster_blacklisted = set()
    artist_blacklisted = set()

    for b in database.get_blacklisted():
        level = b.get("level", "never_play")
        if level == "never_play":
            blacklisted.add(b["song_id"])
        elif level in ("avoid_week", "avoid_today"):
            # Already filtered by expires_at in DB query
            blacklisted.add(b["song_id"])
        elif level == "avoid_mood":
            if b.get("mood"):
                mood_blacklisted.add(b["song_id"])
        elif level == "avoid_artist" and b.get("artist"):
            artist_blacklisted.add(b["artist"].lower())
        elif level == "avoid_similar" and b.get("cluster_ids"):
            try:
                cids = json.loads(b["cluster_ids"])
                if isinstance(cids, list):
                    for cid in cids:
                        cluster_blacklisted.add(cid)
            except (json.JSONDecodeError, TypeError):
                pass

    # Get songs by blacklisted artists
    if artist_blacklisted:
        artist_song_ids = database.get_songs_by_artists(list(artist_blacklisted))
        blacklisted.update(artist_song_ids)

    # Get songs in blacklisted clusters
    if cluster_blacklisted:
        cluster_song_ids = database.get_songs_by_clusters(list(cluster_blacklisted))
        blacklisted.update(cluster_song_ids)

    exclude_ids |= recently_played | blacklisted | mood_blacklisted

    # Apply skip feedback bias to energy/valence ranges
    mood_energy_range, mood_valence_range = _apply_skip_feedback_bias(
        mood_energy_range, mood_valence_range, skip_feedback,
    )

    # If no taste vector, use neutral centroid
    if not taste_vector:
           candidates = search_by_vector(
                    [0.0] * get_embedding_dimension(),
                    n=60,
            exclude_ids=list(exclude_ids),
            min_energy=mood_energy_range[0] if mood_energy_range else None,
            max_energy=mood_energy_range[1] if mood_energy_range else None,
            min_valence=mood_valence_range[0] if mood_valence_range else None,
            max_valence=mood_valence_range[1] if mood_valence_range else None,
            subfolder_filter=subfolder_filter,
        )
    else:
        # ChromaDB search
        candidates = search_by_vector(
            taste_vector,
            n=60,
            exclude_ids=list(exclude_ids),
            min_energy=mood_energy_range[0] if mood_energy_range else None,
            max_energy=mood_energy_range[1] if mood_energy_range else None,
            min_valence=mood_valence_range[0] if mood_valence_range else None,
            max_valence=mood_valence_range[1] if mood_valence_range else None,
            subfolder_filter=subfolder_filter,
        )

    # Cap at 40 for LLM
    return candidates[:40]
