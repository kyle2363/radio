"""Check DB state."""
from db import database
from ingest import embedder
import json

database.init_db()

# Count by ingest_phase
phases = database.get_by_ingest_phase("raw")
analyzed = database.get_by_ingest_phase("analyzed")
described = database.get_by_ingest_phase("described")
embedded = database.get_by_ingest_phase("embedded")
clustered = database.get_by_ingest_phase("clustered")

print(f"raw: {len(phases)}")
print(f"analyzed: {len(analyzed)}")
print(f"described: {len(described)}")
print(f"embedded: {len(embedded)}")
print(f"clustered: {len(clustered)}")
print(f"total: {len(phases)+len(analyzed)+len(described)+len(embedded)+len(clustered)}")

# Check ChromaDB
collection = embedder._get_chroma_client().get_or_create_collection('songs')
print(f"\nChromaDB songs: {collection.count()}")

# Check clusterer
try:
    from ingest import clusterer
    centroids = clusterer.get_or_create_centroids()
    print(f"Cluster centroids: {len(centroids)}")
except Exception as e:
    print(f"Centroids error: {e}")

# Sample song with all fields
song = database.get_song(1)
print(f"\nSample song 1:")
print(f"  ai_description: {song.get('ai_description', '')[:80]}")
print(f"  emotional_tags: {song.get('emotional_tags', '')}")
print(f"  genre_tags: {song.get('genre_tags', '')}")
print(f"  instrument_tags: {song.get('instrument_tags', '')}")
print(f"  energy: {song.get('energy')}")
print(f"  valence: {song.get('valence')}")
print(f"  bpm: {song.get('bpm')}")
print(f"  cluster_id: {song.get('cluster_id')}")
print(f"  ingest_phase: {song.get('ingest_phase')}")

# Check for None values
none_bpm = [s for s in database.get_all_songs() if s.get('bpm') is None]
print(f"\nSongs with bpm=None: {len(none_bpm)}")
none_cluster = [s for s in database.get_all_songs() if s.get('cluster_id') == -1]
print(f"Songs with cluster_id=-1: {len(none_cluster)}")
