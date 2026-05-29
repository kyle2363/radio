"""Delete test song 195."""
from db import database
database.init_db()
conn = database.get_connection()
conn.execute("DELETE FROM songs WHERE id = 195")
conn.commit()
total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
print(f"Deleted song 195. Total songs: {total}")
conn.close()
