"""Fix bytes cluster_id values in DB."""
from db import database
database.init_db()
conn = database.get_connection()

# Find songs with bytes cluster_id
rows = conn.execute("SELECT id, cluster_id FROM songs").fetchall()
fixed = 0
for row in rows:
    sid, cid = row
    if isinstance(cid, bytes):
        # Convert little-endian bytes to int
        new_cid = int.from_bytes(cid, 'little')
        conn.execute("UPDATE songs SET cluster_id = ? WHERE id = ?", (new_cid, sid))
        fixed += 1

conn.commit()
conn.close()
print(f"Fixed {fixed} songs with bytes cluster_id")

# Verify
conn = database.get_connection()
bad = conn.execute("SELECT COUNT(*) FROM songs WHERE typeof(cluster_id) = 'blob'").fetchone()[0]
print(f"Remaining bytes cluster_ids: {bad}")
conn.close()
