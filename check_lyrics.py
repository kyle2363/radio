import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database

database.init_db()
conn = database.get_connection()

# Check schema
schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='songs'").fetchone()[0]
print("=== SONGS SCHEMA ===")
print(schema)
print()

# Count by lyrics status
counts = conn.execute("""
    SELECT 
        SUM(CASE WHEN lyrics IS NOT NULL AND TRIM(lyrics) != '' AND TRIM(lyrics) != 'TODO' THEN 1 ELSE 0 END) as has_lyrics,
        SUM(CASE WHEN lyrics IS NULL OR TRIM(lyrics) = '' OR TRIM(lyrics) = 'TODO' THEN 1 ELSE 0 END) as no_lyrics
    FROM songs
""").fetchone()
print(f"Total: {counts[0] + counts[1]}")
print(f"Has lyrics: {counts[0]}")
print(f"No lyrics: {counts[1]}")

# Show some NULL lyrics examples
rows = conn.execute("""SELECT id, title, lyrics FROM songs 
    WHERE (lyrics IS NULL OR TRIM(lyrics) = '' OR TRIM(lyrics) = 'TODO') LIMIT 5""").fetchall()
print("\nSample songs with no lyrics:")
for r in rows:
    print(f"  {r[0]}: {r[1]} — lyrics={repr(r[2][:50] if r[2] else None)}")

# Check if there's an 'instrument' column at all
cols = [r[1] for r in conn.execute("PRAGMA table_info(songs)").fetchall()]
print(f"\nColumns: {cols}")

conn.close()
