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
from db.chroma_sync import sync_cluster_metadata

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


def _normalize_columns(values: np.ndarray) -> np.ndarray:
    """Scale each scalar feature independently, preserving real zero values."""
    values = np.asarray(values, dtype=float)
    minima = values.min(axis=0)
    ranges = values.max(axis=0) - minima
    ranges[ranges == 0] = 1
    return (values - minima) / ranges


def _scalar_feature_matrix(songs: list) -> np.ndarray:
    """Return normalized energy, valence and BPM columns for every song."""
    energy = np.array([
        0.5 if song.get("energy") is None else song["energy"]
        for song in songs
    ], dtype=float)
    valence = np.array([
        0.5 if song.get("valence") is None else song["valence"]
        for song in songs
    ], dtype=float)
    bpm = np.array([
        120.0 if song.get("bpm") is None else song["bpm"]
        for song in songs
    ], dtype=float) / 180.0
    return _normalize_columns(np.column_stack([energy, valence, bpm]))


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

    def _embedding_map(data):
        if not data or data.get("embeddings") is None:
            return {}
        return {
            str(song_id): embedding
            for song_id, embedding in zip(data.get("ids", []), data["embeddings"])
        }

    audio_map = _embedding_map(audio_data)
    text_map = _embedding_map(text_data)
    ids = set(all_ids)
    has_full_audio = bool(ids) and ids.issubset(audio_map)
    has_full_text = bool(ids) and ids.issubset(text_map)
    scalar_features = _scalar_feature_matrix(songs)

    if has_full_audio and has_full_text:
        audio_embs = np.asarray([audio_map[sid] for sid in all_ids], dtype=float)
        text_embs = np.asarray([text_map[sid] for sid in all_ids], dtype=float)
        audio_dim = audio_embs.shape[1]
        text_dim = text_embs.shape[1]
        print(f"  Blending MuQ audio ({audio_dim}-dim) + text ({text_dim}-dim) embeddings ({len(songs)} songs)")
        feature_matrix = np.hstack([
            audio_embs * 0.60,
            text_embs * 0.30,
            scalar_features * np.array([0.05, 0.03, 0.02]),
        ])
        return feature_matrix, [s["id"] for s in songs], (audio_dim, text_dim)

    if has_full_audio:
        audio_embs = np.asarray([audio_map[sid] for sid in all_ids], dtype=float)
        audio_dim = audio_embs.shape[1]
        print(f"  Using MuQ audio embeddings only ({audio_dim}-dim, {len(songs)} songs)")
        feature_matrix = np.hstack([
            audio_embs * 0.90,
            scalar_features * np.array([0.05, 0.03, 0.02]),
        ])
        return feature_matrix, [s["id"] for s in songs], (audio_dim, 0)

    if has_full_text:
        text_embs = np.asarray([text_map[sid] for sid in all_ids], dtype=float)
        text_dim = text_embs.shape[1]
        print(f"  Using text embeddings only ({text_dim}-dim, {len(songs)} songs)")
        feature_matrix = np.hstack([
            text_embs * 0.85,
            scalar_features * np.array([0.10, 0.03, 0.02]),
        ])
        return feature_matrix, [s["id"] for s in songs], (0, text_dim)

    # Never cluster a subset: use metadata features for every song until at
    # least one embedding collection covers the complete eligible song set.
    print("  WARNING: no complete embedding coverage, using energy/valence/BPM for all songs")
    return scalar_features, [s["id"] for s in songs], (0, 0)


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
        n_clusters = 1 if len(songs) == 1 else max(2, len(songs) // 2)
        print(f"  Only {len(songs)} songs, using {n_clusters} clusters")
    else:
        n_clusters = config.N_CLUSTERS

    print(f"  Building feature matrix for {len(songs)} songs...")
    feature_matrix, song_ids, emb_dims = _build_feature_matrix(songs)

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

    try:
        refreshed = sync_cluster_metadata(dict(zip(song_ids, map(int, cluster_labels))))
        print(f"  Refreshed cluster metadata: {refreshed}")
    except Exception as exc:
        raise RuntimeError('Clusters saved in SQLite, but embedding metadata refresh failed; '
                           'rerun Cluster to retry. Recommendations use SQLite assignments.') from exc

    # Print cluster sizes
    unique, counts = np.unique(cluster_labels, return_counts=True)
    for cid, count in zip(unique, counts):
        print(f"  Cluster {cid}: {count} songs")

    return song_ids
