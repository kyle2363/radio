import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database

database.init_db()
conn = database.get_connection()
conn.execute("UPDATE songs SET ingest_phase = 'raw'")
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"Reset {count} songs to 'raw'")
conn.close()
