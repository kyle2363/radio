"""KMeans clustering for songs based on embeddings and audio features."""

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


def _get_chroma_collection():
    """Get the ChromaDB collection for song embeddings."""
    os.environ["CHROMA_NO_ANALYTICS"] = "1"
    os.environ["CHROMA_TELEMETRY_OFF"] = "1"
    import chromadb
    os.makedirs(config.CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return client.get_collection("songs")


def _build_feature_matrix(songs: list) -> tuple:
    """Build a feature matrix from song data.

    Returns (feature_matrix, song_ids) where feature_matrix is numpy array.
    Uses ChromaDB embeddings if available, falls back to audio features.
    """
    chroma_collection = _get_chroma_collection()

    # Try to get embeddings from ChromaDB
    all_ids = [str(s["id"]) for s in songs]
    try:
        data = chroma_collection.get(
            ids=all_ids,
            include=["embeddings", "metadatas"],
        )
        if data and data["embeddings"]:
            embeddings = np.array(data["embeddings"])

            # Also include audio features for better clustering
            energy = np.array([(s.get("energy") or 0.5) for s in songs]).reshape(-1, 1)
            valence = np.array([(s.get("valence") or 0.5) for s in songs]).reshape(-1, 1)
            bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in songs]).reshape(-1, 1)

            # Embeddings are high-dimensional (4096 for qwen3-embedding:4b)
            # Weight audio features slightly to influence cluster boundaries
            feature_matrix = np.hstack([
                embeddings * 0.85,
                energy * 0.1,
                valence * 0.03,
                bpm_norm * 0.02,
            ])
            return feature_matrix, [s["id"] for s in songs]
    except Exception as e:
        print(f"  ChromaDB embedding lookup failed: {e}, falling back to audio features")

    # Fallback: use only audio features
    energy = np.array([(s.get("energy") or 0.5) for s in songs]).reshape(-1, 1)
    valence = np.array([(s.get("valence") or 0.5) for s in songs]).reshape(-1, 1)
    bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in songs]).reshape(-1, 1)

    feature_matrix = np.hstack([energy, valence, bpm_norm])
    return feature_matrix, [s["id"] for s in songs]


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
    feature_matrix, song_ids = _build_feature_matrix(songs)

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
        for song_id, cluster_id in zip(song_ids, cluster_labels):
            conn.execute(
                "UPDATE songs SET cluster_id = ? WHERE id = ?",
                (int(cluster_id), song_id),
            )
            conn.execute(
                "UPDATE songs SET ingest_phase = 'clustered' WHERE id = ?",
                (song_id,),
            )
        conn.commit()
    finally:
        conn.close()

    # Store cluster centroids in ChromaDB for mood lookups
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
        centroids_collection.delete(where={})
        centroids_collection.upsert(
            ids=[f"centroid_{i}" for i in range(n_clusters)],
            embeddings=kmeans.cluster_centers_.tolist(),
            metadatas=[{
                "cluster_id": i,
                "song_count": int(np.sum(cluster_labels == i)),
            } for i in range(n_clusters)],
        )
    except Exception as e:
        print(f"  Warning: could not save centroids to ChromaDB: {e}")

    # Print cluster sizes
    unique, counts = np.unique(cluster_labels, return_counts=True)
    for cid, count in zip(unique, counts):
        print(f"  Cluster {cid}: {count} songs")

    return song_ids
