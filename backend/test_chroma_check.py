"""Check ChromaDB for the 5 remaining songs."""
import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

import chromadb
from config import CHROMA_PATH

client = chromadb.PersistentClient(path=CHROMA_PATH)
songs = client.get_collection("songs")

for song_id in [1, 2, 3, 4, 5]:
    result = songs.get(ids=[str(song_id)])
    print(f"Song {song_id}: found={len(result['ids']) > 0}")
    if len(result['ids']) > 0:
        print(f"  metadata: {result['metadatas'][0]}")
        print(f"  embedding dim: {len(result['embeddings'][0])}")
    else:
        print(f"  NOT in ChromaDB")

# Total count
total = songs.count()
print(f"\nTotal ChromaDB 'songs' vectors: {total}")
