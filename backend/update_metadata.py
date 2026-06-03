"""Update ChromaDB metadata to add subfolder field without re-embedding."""
import sys
import os
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"

sys.path.insert(0, "backend")
import chromadb
import config
from db import database

client = chromadb.PersistentClient(path=config.CHROMA_PATH)
collection = client.get_collection("songs")

all_ids = database.get_all_song_ids()
print(f"Found {len(all_ids)} songs")

# Get existing data from ChromaDB
existing = collection.get(include=["embeddings", "metadatas"])
existing_ids = set(existing["ids"])
print(f"Found {len(existing_ids)} songs in ChromaDB")

updated = 0
for sid in all_ids:
    sid_str = str(sid)
    if sid_str not in existing_ids:
        print(f"  Skipping {sid} (not in ChromaDB)")
        continue

    idx = existing["ids"].index(sid_str)
    embedding = existing["embeddings"][idx]
    meta = dict(existing["metadatas"][idx]) if existing["metadatas"][idx] else {}

    # Add subfolder from SQLite
    song = database.get_song(sid)
    if song:
        meta["subfolder"] = song.get("subfolder") or ""

    # Upsert with same embedding + updated metadata
    collection.update(
        ids=[sid_str],
        embeddings=[embedding],
        metadatas=[meta],
    )
    updated += 1
    if updated % 100 == 0:
        print(f"  Updated {updated}/{len(all_ids)}...")

print(f"Done: updated {updated} songs")
