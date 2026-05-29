"""Mark 5 songs as embedded and re-cluster."""
import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

import chromadb
from config import CHROMA_PATH
from db import database
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import numpy as np

database.init_db()

# Mark the 5 songs as embedded
for song_id in [1, 2, 3, 4, 5]:
    database.set_ingest_phase(song_id, "embedded")
    print(f"  Marked song {song_id} as embedded")

# Re-cluster
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection("songs")

all_songs = collection.get(include=["metadatas", "embeddings"])
song_ids = []
metadatas = []

for i, sid in enumerate(all_songs["ids"]):
    meta = all_songs["metadatas"][i]
    song_ids.append(int(sid))
    metadatas.append(meta)

print(f"\nFound {len(song_ids)} songs in ChromaDB")

# Build feature matrix: embedding (1024) + energy (1) + valence (1) + bpm (1)
# Normalize to [0,1] for audio features
features = []
for meta in metadatas:
    embedding = meta.get("embedding")
    if embedding is None:
        # Get embedding from ChromaDB
        pass
    energy = meta.get("energy") or 0.5
    valence = meta.get("valence") or 0.5
    bpm = meta.get("bpm") or 120
    features.append({
        "energy": energy,
        "valence": valence,
        "bpm": bpm,
    })

# Get embeddings from ChromaDB
all_data = collection.get(include=["embeddings", "metadatas"])
all_embeddings = all_data["embeddings"] or []
print(f"Got {len(all_embeddings)} embeddings")

# Build feature matrix: embeddings + normalized audio features
audio_features = []
for meta in metadatas:
    energy = (meta.get("energy") or 0.5)
    valence = (meta.get("valence") or 0.5)
    bpm = (meta.get("bpm") or 120) / 200.0  # normalize to ~[0,1]
    audio_features.append([energy, valence, bpm])

audio_arr = np.array(audio_features)

# Combine: use embeddings as primary features, append normalized audio features
combined = []
for i, emb in enumerate(all_embeddings):
    if emb is not None:
        # Concatenate embedding with scaled audio features
        audio = audio_arr[i] * 0.15
        feature_vec = np.concatenate([np.array(emb) * 0.85, audio])
        combined.append(feature_vec)
    else:
        combined.append(audio_arr[i])

combined_arr = np.array(combined)
print(f"Feature matrix shape: {combined_arr.shape}")

# KMeans
n_clusters = 18
kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
labels = kmeans.fit_predict(combined_arr)

# Save cluster assignments
conn = database.get_connection()
for song_id, cluster_id in zip(song_ids, labels):
    conn.execute(
        "UPDATE songs SET cluster_id = ? WHERE id = ?",
        (int(cluster_id), song_id),
    )
conn.commit()

# Set ingest_phase for all songs
for song_id in song_ids:
    database.set_ingest_phase(song_id, "clustered")

# Print cluster sizes
from collections import Counter
cluster_counts = Counter(labels)
print(f"\nClustered {len(song_ids)} songs into {n_clusters} clusters:")
for cluster_id in sorted(cluster_counts.keys()):
    print(f"  Cluster {cluster_id}: {cluster_counts[cluster_id]} songs")

# Verify
status = database.get_ingest_status()
print(f"\nIngest status: {status}")
print(f"\nChromaDB vectors: {collection.count()}")
