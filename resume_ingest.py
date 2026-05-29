import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database
from ingest import embedder, clusterer

database.init_db()
conn = database.get_connection()

# Get all non-clustered song IDs
rows = conn.execute(
    "SELECT id FROM songs WHERE ingest_phase != 'clustered'"
).fetchall()
non_clustered = [r[0] for r in rows]
print(f"Found {len(non_clustered)} non-clustered songs")

if not non_clustered:
    print("All songs already clustered!")
    conn.close()
    sys.exit(0)

# Embed them
print(f"\nEmbedding {len(non_clustered)} songs...")
embedded_ids = embedder.embed_described_songs(non_clustered)
print(f"Embedded {len(embedded_ids)} songs")

# Cluster
print(f"\nClustering...")
clustered_ids = clusterer.cluster_songs()
print(f"Clustered {len(clustered_ids)} songs")

# Verify
rows = conn.execute("SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

conn.close()
print("\nDone!")
