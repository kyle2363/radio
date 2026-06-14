"""KMeans clustering for songs based on embeddings and audio features.

Prefers MuQ-MuLan audio embeddings for clustering when available,
falls back to text embeddings from the LLM provider.
"""

import json
import os

import numpy as np
from sklearn.cluster import KMeans

import config
from db import database

_cluster_model = None
_cluster_centroids = None


def _get_all_embedded_songs():
    """Get all songs that have been embedded, with their features."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT id, filepath, artist, title, energy, valence, bpm,
                      cluster_id, emotional_tags, genre_tags, instrument_tags,
                      ai_description
               FROM songs
               WHERE ingest_phase IN ('embedded', 'clustered')
               ORDER BY id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_chroma_collection(name="songs"):
    """Get a ChromaDB collection by name."""
    os.environ["CHROMA_NO_ANALYTICS"] = "1"
    os.environ["CHROMA_TELEMETRY_OFF"] = "1"
    import chromadb
    os.makedirs(config.CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


def _build_feature_matrix(songs: list) -> tuple:
    """Build a feature matrix from song data.

    Blends MuQ-MuLan audio embeddings and text embeddings for richer
    clustering (artist names in text embeds help group same-artist songs).
    Falls back gracefully if one or both embedding types are missing.

    Returns (feature_matrix, song_ids, emb_dims) where
      feature_matrix is numpy array,
      emb_dims is (audio_dim, text_dim) for centroid slicing.
    """
    all_ids = [str(s["id"]) for s in songs]

    # Fetch both audio and text embeddings in parallel
    audio_data = None
    text_data = None

    if config.MUQ_ENABLED:
        try:
            audio_coll = _get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
            audio_data = audio_coll.get(ids=all_ids, include=["embeddings"])
        except Exception as e:
            print(f"  Audio embedding lookup failed: {e}")

    try:
        text_coll = _get_chroma_collection("songs")
        text_data = text_coll.get(ids=all_ids, include=["embeddings"])
    except Exception as e:
        print(f"  Text embedding lookup failed: {e}")

    has_audio = audio_data and audio_data.get("embeddings") is not None and len(audio_data["embeddings"]) > 0
    has_text = text_data and text_data.get("embeddings") is not None and len(text_data["embeddings"]) > 0

    if has_audio and has_text:
        # Blend: audio + text carries artist info for same-artist grouping
        audio_map = dict(zip(audio_data["ids"], audio_data["embeddings"]))
        text_map = dict(zip(text_data["ids"], text_data["embeddings"]))
        valid_songs = [s for s in songs if str(s["id"]) in audio_map and str(s["id"]) in text_map]

        if valid_songs:
            audio_embs = np.array([audio_map[str(s["id"])] for s in valid_songs])
            text_embs = np.array([text_map[str(s["id"])] for s in valid_songs])
            audio_dim = audio_embs.shape[1]
            text_dim = text_embs.shape[1]
            print(f"  Blending MuQ audio ({audio_dim}-dim) + text ({text_dim}-dim) embeddings ({len(valid_songs)} songs)")

            energy = np.array([(s.get("energy") or 0.5) for s in valid_songs]).reshape(-1, 1)
            valence = np.array([(s.get("valence") or 0.5) for s in valid_songs]).reshape(-1, 1)
            bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in valid_songs]).reshape(-1, 1)

            feature_matrix = np.hstack([
                audio_embs * 0.60,
                text_embs * 0.30,
                energy * 0.05,
                valence * 0.03,
                bpm_norm * 0.02,
            ])
            return feature_matrix, [s["id"] for s in valid_songs], (audio_dim, text_dim)

    if has_audio:
        # Audio only
        audio_map = dict(zip(audio_data["ids"], audio_data["embeddings"]))
        valid_songs = [s for s in songs if str(s["id"]) in audio_map]
        audio_embs = np.array([audio_map[str(s["id"])] for s in valid_songs])
        audio_dim = audio_embs.shape[1]
        print(f"  Using MuQ audio embeddings only ({audio_dim}-dim, {len(valid_songs)} songs)")

        energy = np.array([(s.get("energy") or 0.5) for s in valid_songs]).reshape(-1, 1)
        valence = np.array([(s.get("valence") or 0.5) for s in valid_songs]).reshape(-1, 1)
        bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in valid_songs]).reshape(-1, 1)

        feature_matrix = np.hstack([
            audio_embs * 0.90,
            energy * 0.05,
            valence * 0.03,
            bpm_norm * 0.02,
        ])
        return feature_matrix, [s["id"] for s in valid_songs], (audio_dim, 0)

    if has_text:
        # Text only
        text_map = dict(zip(text_data["ids"], text_data["embeddings"]))
        valid_songs = [s for s in songs if str(s["id"]) in text_map]
        text_embs = np.array([text_map[str(s["id"])] for s in valid_songs])
        text_dim = text_embs.shape[1]
        print(f"  Using text embeddings only ({text_dim}-dim, {len(valid_songs)} songs)")

        energy = np.array([(s.get("energy") or 0.5) for s in valid_songs]).reshape(-1, 1)
        valence = np.array([(s.get("valence") or 0.5) for s in valid_songs]).reshape(-1, 1)
        bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in valid_songs]).reshape(-1, 1)

        feature_matrix = np.hstack([
            text_embs * 0.85,
            energy * 0.10,
            valence * 0.03,
            bpm_norm * 0.02,
        ])
        return feature_matrix, [s["id"] for s in valid_songs], (0, text_dim)

    # Fallback: audio features only
    print("  WARNING: no embeddings found, using energy/valence/BPM only")
    energy = np.array([(s.get("energy") or 0.5) for s in songs]).reshape(-1, 1)
    valence = np.array([(s.get("valence") or 0.5) for s in songs]).reshape(-1, 1)
    bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in songs]).reshape(-1, 1)

    feature_matrix = np.hstack([energy, valence, bpm_norm])
    return feature_matrix, [s["id"] for s in songs], (0, 0)


def cluster_songs() -> list:
    """Run KMeans clustering on all embedded songs.

    Returns list of song IDs that were clustered.
    """
    songs = _get_all_embedded_songs()
    if not songs:
        print("  No embedded songs to cluster")
        return []

    if len(songs) < config.N_CLUSTERS:
        # Can't cluster fewer songs than clusters
        n_clusters = max(2, len(songs) // 2)
        print(f"  Only {len(songs)} songs, using {n_clusters} clusters")
    else:
        n_clusters = config.N_CLUSTERS

    print(f"  Building feature matrix for {len(songs)} songs...")
    feature_matrix, song_ids, emb_dims = _build_feature_matrix(songs)

    # Normalize features to [0, 1] range for better clustering
    # Embeddings are already near-normalized, but audio features need scaling
    audio_mask = slice(feature_matrix.shape[1] - 3, feature_matrix.shape[1])
    audio_features = feature_matrix[:, audio_mask]
    audio_min = audio_features.min(axis=0)
    audio_max = audio_features.max(axis=0)
    audio_range = audio_max - audio_min
    audio_range[audio_range == 0] = 1  # avoid division by zero
    feature_matrix[:, audio_mask] = (audio_features - audio_min) / audio_range

    print(f"  Running KMeans with {n_clusters} clusters on {len(song_ids)} songs...")
    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=42,
        n_init=10,
        max_iter=300,
    )
    cluster_labels = kmeans.fit_predict(feature_matrix)

    # Save cluster assignments to SQLite
    print("  Saving cluster assignments...")
    conn = database.get_connection()
    try:
        conn.execute("BEGIN")
        cluster_updates = [
            (int(cluster_id), song_id)
            for song_id, cluster_id in zip(song_ids, cluster_labels)
        ]
        conn.executemany(
            "UPDATE songs SET cluster_id = ? WHERE id = ?",
            cluster_updates,
        )
        phase_updates = [(song_id,) for song_id in song_ids]
        conn.executemany(
            "UPDATE songs SET ingest_phase = 'clustered' WHERE id = ?",
            phase_updates,
        )
        conn.commit()
    finally:
        conn.close()

    # Store cluster centroids in ChromaDB for mood lookups
    # Store only the combined embedding portion (not audio features)
    audio_dim, text_dim = emb_dims
    emb_dim = audio_dim + text_dim
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        import chromadb
        os.makedirs(config.CHROMA_PATH, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)

        centroids_collection = chroma_client.get_or_create_collection(
            "cluster_centroids",
            metadata={"hnsw:space": "cosine"},
        )
        old = centroids_collection.get(include=[])
        if old and old["ids"]:
            centroids_collection.delete(ids=old["ids"])

        if emb_dim > 0:
            # Extract embedding columns (first emb_dim columns before energy/valence/bpm)
            embedding_centers = kmeans.cluster_centers_[:, :emb_dim]
            centroids_collection.add(
                ids=[f"centroid_{i}" for i in range(n_clusters)],
                embeddings=embedding_centers.tolist(),
                metadatas=[{
                    "cluster_id": i,
                    "song_count": int(np.sum(cluster_labels == i)),
                    "audio_dim": audio_dim,
                    "text_dim": text_dim,
                } for i in range(n_clusters)],
            )
        else:
            centroids_collection.add(
                ids=[f"centroid_{i}" for i in range(n_clusters)],
                embeddings=kmeans.cluster_centers_.tolist(),
                metadatas=[{
                    "cluster_id": i,
                    "song_count": int(np.sum(cluster_labels == i)),
                    "embedding_source": "features_only",
                } for i in range(n_clusters)],
            )
    except Exception as e:
        print(f"  Warning: could not save centroids to ChromaDB: {e}")

    # Print cluster sizes
    unique, counts = np.unique(cluster_labels, return_counts=True)
    for cid, count in zip(unique, counts):
        print(f"  Cluster {cid}: {count} songs")

    return song_ids
