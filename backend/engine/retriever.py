"""ChromaDB vector retrieval for song recommendations."""

import math
import random
from datetime import datetime, timedelta
import config
import chromadb
from db import database


def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_chroma_collection(name="songs"):
    """Get a ChromaDB collection by name."""
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return client.get_collection(name)


def _jitter_vector(v, strength=0.03):
    """Add small random noise to a vector for variety between calls.
    
    This prevents the same candidates from being returned every time
    while preserving the overall direction of the query.
    """
    noise = [random.gauss(0, strength) for _ in v]
    noise_mag = math.sqrt(sum(x * x for x in noise))
    if noise_mag > 0:
        noise = [x / noise_mag * strength for x in noise]
    return [a + b for a, b in zip(v, noise)]


def search_by_vector(taste_vector, n=60, exclude_ids=None, min_energy=None, max_energy=None, min_valence=None, max_valence=None):
    """Search ChromaDB by embedding vector with optional filters.

    Returns list of dicts with song_id, score, metadata.
    """
    if exclude_ids is None:
        exclude_ids = []

    collection = get_chroma_collection()
    # Add small jitter to taste vector for variety between calls
    query_vector = _jitter_vector(taste_vector)
    # Inflate query to get a broader pool, then subsample
    inflated_n = min(max(n * 2, 120), 200)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=inflated_n,
        include=["distances", "metadatas"],
    )

    song_ids = []
    for i in range(len(results["ids"][0])):
        song_id = int(results["ids"][0][i])
        if song_id in exclude_ids:
            continue

        song_ids.append(song_id)

    # Fetch artist/title from SQLite since ChromaDB metadata may be stale/empty
    song_info = {}
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
    for i in range(len(results["ids"][0])):
        song_id = int(results["ids"][0][i])
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
        n_results=min(n, 200),
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


def build_candidate_list(taste_vector, current_song_id, exclude_ids=None, mood_energy_range=None, mood_valence_range=None, repulsion_vector=None):
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

    # Get blacklisted song IDs
    blacklisted = set()
    for b in database.get_blacklisted():
        blacklisted.add(b["song_id"])

    exclude_ids |= recently_played | blacklisted

    # If no taste vector, use neutral centroid
    if not taste_vector:
        candidates = search_by_vector(
            [0.0] * 1024,
            n=60,
            exclude_ids=list(exclude_ids),
            min_energy=mood_energy_range[0] if mood_energy_range else None,
            max_energy=mood_energy_range[1] if mood_energy_range else None,
            min_valence=mood_valence_range[0] if mood_valence_range else None,
            max_valence=mood_valence_range[1] if mood_valence_range else None,
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
        )

    # Cap at 40 for LLM
    return candidates[:40]
