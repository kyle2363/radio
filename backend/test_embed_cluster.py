"""Re-embed and cluster all songs (LLM tags already done)."""
from db import database
from ingest import embedder, clusterer

database.init_db()

# Get all described songs
songs = database.get_by_ingest_phase("described")
print(f"Found {len(songs)} songs to embed")

if not songs:
    print("No songs to embed!")
    exit()

# Get all song IDs
song_ids = [s['id'] for s in songs]

# Embed all
print("\nEmbedding...")
embedded_ids = embedder.embed_described_songs(song_ids)
print(f"Embedded {len(embedded_ids)} songs")

# Cluster
print("\nClustering...")
clustered_ids = clusterer.cluster_songs()
print(f"Clustered {len(clustered_ids)} songs")

# Verify
print("\nVerification:")
remaining = database.get_by_ingest_phase("described")
clustered = database.get_by_ingest_phase("clustered")
print(f"  Still described: {len(remaining)}")
print(f"  Now clustered: {len(clustered)}")

# ChromaDB check
chroma_count = embedder._get_chroma_client().get_or_create_collection('songs').count()
print(f"  ChromaDB vectors: {chroma_count}")
