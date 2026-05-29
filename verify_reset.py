import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database
import config

print(f"DB path: {config.DB_PATH}")
import os
print(f"DB exists: {os.path.exists(config.DB_PATH)}")

database.init_db()
conn = database.get_connection()
rows = conn.execute("SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase").fetchall()
print(f"Phase before: {rows}")

conn.execute("UPDATE songs SET ingest_phase = 'raw'")
conn.commit()

rows = conn.execute("SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase").fetchall()
print(f"Phase after: {rows}")

# Verify lyrics
lyrics = conn.execute("SELECT COUNT(*) FROM songs WHERE lyrics IS NOT NULL AND TRIM(lyrics) != '' AND TRIM(lyrics) != 'TODO'").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"Lyrics: {lyrics}/{total}")

conn.close()
