"""Check which song has no file."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import os
from db import database
database.init_db()

conn = database.get_connection()
rows = conn.execute("SELECT id, filepath FROM songs").fetchall()
conn.close()

for row in rows:
    sid, fp = row
    if not os.path.exists(fp):
        print(f"MISSING: ID={sid} filepath={fp}")
