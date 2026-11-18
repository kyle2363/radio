"""Reset all songs to described phase so pipeline re-embeds and clusters."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def main():
    from db import database
    database.init_db()
    conn = database.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    prompt = f"Reset all {total} songs to 'described' (every song will be re-embedded and re-clustered)? Type 'yes': "
    if input(prompt).strip().lower() != "yes":
        conn.close()
        raise SystemExit("Nothing was changed.")
    conn.execute("UPDATE songs SET ingest_phase = 'described'")
    conn.commit()
    conn.close()
    print(f"Reset all {total} songs to 'described' phase")


if __name__ == "__main__":
    main()
