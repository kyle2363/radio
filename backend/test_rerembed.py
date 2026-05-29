"""Delete 5 songs with no embeddings from ChromaDB, then re-embed them."""
import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

import chromadb
import json
from config import CHROMA_PATH
from db import database
from llm import llm_embed

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection("songs")

# Delete the 5 songs with no embeddings
bad_ids = [1, 2, 3, 4, 5]
collection.delete(ids=[str(sid) for sid in bad_ids])
print(f"Deleted {len(bad_ids)} songs from ChromaDB")

# Verify
remaining = collection.count()
print(f"ChromaDB vectors after delete: {remaining}")

# Re-embed the 5 songs

for song_id in bad_ids:
    song = database.get_song(song_id)
    if not song:
        print(f"  Song {song_id} not found in DB")
        continue

    # Build embedding text
    parts = []
    if song.get("artist"):
        parts.append(f"artist: {song['artist']}")
    if song.get("title"):
        parts.append(f"title: {song['title']}")
    if song.get("genre_tags"):
        try:
            parts.append(f"genres: {', '.join(json.loads(song['genre_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("emotional_tags"):
        try:
            parts.append(f"mood: {', '.join(json.loads(song['emotional_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("instrument_tags"):
        try:
            parts.append(f"instruments: {', '.join(json.loads(song['instrument_tags']))}")
        except (json.JSONDecodeError, TypeError):
            pass
    if song.get("ai_description"):
        parts.append(f"description: {song['ai_description'][:300]}")
    if song.get("energy") is not None:
        parts.append(f"energy: {song['energy']}")
    if song.get("valence") is not None:
        parts.append(f"valence: {song['valence']}")
    if song.get("bpm"):
        parts.append(f"bpm: {song['bpm']}")

    embedding_text = " | ".join(parts) if parts else "unknown song"
    print(f"\nSong {song_id}: embedding_text = {embedding_text[:100]}...")

    try:
        result = llm_embed().embed(model=None, input=embedding_text)
        embedding = result["embeddings"][0]
        print(f"  Embedding dim: {len(embedding) if embedding else 'None'}")

        if embedding:
            collection.upsert(
                ids=[str(song_id)],
                embeddings=[embedding],
                metadatas=[{
                    "song_id": song_id,
                    "artist": song.get("artist") or "",
                    "title": song.get("title") or "",
                    "energy": song.get("energy") or 0.5,
                    "valence": song.get("valence") or 0.5,
                    "cluster_id": song.get("cluster_id") if song.get("cluster_id") is not None else -1,
                }],
            )
            print(f"  Upserted to ChromaDB")
        else:
            print(f"  ERROR: embedding is None!")
    except Exception as e:
        print(f"  ERROR: {e}")

# Verify final state
final = collection.count()
print(f"\nChromaDB vectors after re-embed: {final}")

# Check ingest status
status = database.get_ingest_status()
print(f"\nIngest status: {status}")
