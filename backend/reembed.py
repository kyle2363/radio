"""Re-embed all songs to update ChromaDB metadata (add subfolder field)."""
import os
os.environ["CHROMA_NO_ANALYTICS"] = "1"
os.environ["CHROMA_TELEMETRY_OFF"] = "1"
import logging
logging.getLogger("chromadb").setLevel(logging.ERROR)
logging.getLogger("posthog").setLevel(logging.ERROR)
try:
    import posthog
    posthog.disabled = True
    posthog.capture = lambda *a, **k: None
except ImportError:
    pass

import sys
sys.path.insert(0, "backend")

from db import database
from ingest.embedder import embed_song

ids = database.get_all_song_ids()
print(f"Re-embedding {len(ids)} songs...")
ok = 0
fail = 0
for i, sid in enumerate(ids):
    song = database.get_song(sid)
    if song and embed_song(sid, song):
        ok += 1
    else:
        fail += 1
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(ids)}...")
print(f"Done: {ok} ok, {fail} failed")
