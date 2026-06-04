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

    Prefers MuQ-MuLan audio embeddings when available, falls back to
    text embeddings. Always includes audio features (energy, valence, BPM).

    Returns (feature_matrix, song_ids) where feature_matrix is numpy array.
    """
    all_ids = [str(s["id"]) for s in songs]

    # Try audio embeddings first (MuQ-MuLan)
    if config.MUQ_ENABLED:
        try:
            audio_collection = _get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
            data = audio_collection.get(
                ids=all_ids,
                include=["embeddings", "metadatas"],
            )
            if data and data["embeddings"]:
                # Build mapping from ID to embedding for alignment
                id_to_emb = dict(zip(data["ids"], data["embeddings"]))
                valid_songs = [s for s in songs if str(s["id"]) in id_to_emb]
                embeddings = np.array([id_to_emb[str(s["id"])] for s in valid_songs])
                print(f"  Using MuQ-MuLan audio embeddings ({embeddings.shape[1]}-dim, {len(valid_songs)}/{len(songs)} songs)")

                energy = np.array([(s.get("energy") or 0.5) for s in valid_songs]).reshape(-1, 1)
                valence = np.array([(s.get("valence") or 0.5) for s in valid_songs]).reshape(-1, 1)
                bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in valid_songs]).reshape(-1, 1)

                feature_matrix = np.hstack([
                    embeddings * 0.90,
                    energy * 0.05,
                    valence * 0.03,
                    bpm_norm * 0.02,
                ])
                return feature_matrix, [s["id"] for s in valid_songs]
        except Exception as e:
            print(f"  Audio embedding lookup failed: {e}, falling back to text embeddings")

    # Fallback: text embeddings from the LLM provider
    try:
        text_collection = _get_chroma_collection("songs")
        data = text_collection.get(
            ids=all_ids,
            include=["embeddings", "metadatas"],
        )
        if data and data["embeddings"]:
            # Build mapping from ID to embedding for alignment
            id_to_emb = dict(zip(data["ids"], data["embeddings"]))
            valid_songs = [s for s in songs if str(s["id"]) in id_to_emb]
            embeddings = np.array([id_to_emb[str(s["id"])] for s in valid_songs])
            print(f"  Using text embeddings ({embeddings.shape[1]}-dim, {len(valid_songs)}/{len(songs)} songs)")

            energy = np.array([(s.get("energy") or 0.5) for s in valid_songs]).reshape(-1, 1)
            valence = np.array([(s.get("valence") or 0.5) for s in valid_songs]).reshape(-1, 1)
            bpm_norm = np.array([(s.get("bpm") or 120) / 180.0 for s in valid_songs]).reshape(-1, 1)

            feature_matrix = np.hstack([
                embeddings * 0.85,
                energy * 0.1,
                valence * 0.03,
                bpm_norm * 0.02,
            ])
            return feature_matrix, [s["id"] for s in valid_songs]
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
    # Store only the embedding portion (not audio features) so centroids
    # are in the same space as individual song embeddings
    try:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        import chromadb
        os.makedirs(config.CHROMA_PATH, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)

        # Determine which collection the embeddings came from
        if config.MUQ_ENABLED:
            try:
                audio_coll = chroma_client.get_collection(config.MUQ_AUDIO_COLLECTION)
                sample = audio_coll.get(limit=1, include=["embeddings"])
                if sample and sample["embeddings"]:
                    emb_dim = len(sample["embeddings"][0])
                    emb_source = config.MUQ_AUDIO_COLLECTION
                else:
                    emb_dim = 0
                    emb_source = None
            except Exception:
                emb_dim = 0
                emb_source = None
        else:
            emb_dim = 0
            emb_source = None

        if emb_dim == 0:
            try:
                text_coll = chroma_client.get_collection("songs")
                sample = text_coll.get(limit=1, include=["embeddings"])
                if sample and sample["embeddings"]:
                    emb_dim = len(sample["embeddings"][0])
                    emb_source = "songs"
            except Exception:
                emb_source = None

        # Extract embedding portion from KMeans cluster centers
        # feature_matrix has structure: [embedding_cols..., energy, valence, bpm]
        # The last 3 columns are audio features
        embedding_centers = kmeans.cluster_centers_[:, :emb_dim] if emb_dim else None

        centroids_collection = chroma_client.get_or_create_collection(
            "cluster_centroids",
            metadata={"hnsw:space": "cosine"},
        )
        # Clear old centroids
        old = centroids_collection.get(include=[])
        if old and old["ids"]:
            centroids_collection.delete(ids=old["ids"])

        if embedding_centers is not None:
            centroids_collection.upsert(
                ids=[f"centroid_{i}" for i in range(n_clusters)],
                embeddings=embedding_centers.tolist(),
                metadatas=[{
                    "cluster_id": i,
                    "song_count": int(np.sum(cluster_labels == i)),
                    "embedding_source": emb_source or "unknown",
                } for i in range(n_clusters)],
            )
        else:
            # Fallback: store raw cluster centers (will only match in full feature space)
            centroids_collection.upsert(
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
