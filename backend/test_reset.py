"""Reset all songs to described phase so pipeline re-embeds and clusters."""
from db import database
database.init_db()
conn = database.get_connection()
conn.execute("UPDATE songs SET ingest_phase = 'described'")
conn.commit()
total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"Reset all {total} songs to 'described' phase")
conn.close()
