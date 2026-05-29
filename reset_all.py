import sys, shutil, os
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database
import config

database.init_db()
conn = database.get_connection()

# Delete ChromaDB
if os.path.exists(config.CHROMA_PATH):
    shutil.rmtree(config.CHROMA_PATH)
    print(f"Deleted ChromaDB: {config.CHROMA_PATH}")

# Reset all songs to raw
conn.execute("UPDATE songs SET ingest_phase = 'raw'")
count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"Reset all {count} songs to 'raw'")

conn.close()
