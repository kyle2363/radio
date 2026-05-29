import sys
import os
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"

# Patch posthog.capture BEFORE importing chromadb
import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database
from ingest import embedder, clusterer, pipeline

database.init_db()
conn = database.get_connection()

# Count songs at each phase before
rows = conn.execute("SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase").fetchall()
print("Before:")
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# Run the full ingest — it will handle analyzed->described->embedded->clustered
print("\nRunning ingest pipeline...")
result = pipeline.run_ingest()
print(f"\nResult: {result}")

# Final count
rows = conn.execute("SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase").fetchall()
print("\nAfter:")
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# Check lyrics
has_lyrics = conn.execute("SELECT COUNT(*) FROM songs WHERE lyrics IS NOT NULL AND TRIM(lyrics) != '' AND TRIM(lyrics) != 'TODO'").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"\nLyrics: {has_lyrics}/{total} songs have lyrics")

conn.close()
