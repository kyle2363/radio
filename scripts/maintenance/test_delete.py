"""Delete one song row (and its history) from the production database.

Usage: python scripts/maintenance/test_delete.py SONG_ID
The audio file is not touched.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit(__doc__)
    song_id = int(sys.argv[1])

    from db import database
    database.init_db()
    song = database.get_song(song_id)
    if not song:
        raise SystemExit(f"No song with id {song_id}.")
    print(f"Song {song_id}: {song.get('artist') or ''} - {song.get('title') or ''}")
    print(f"  {song.get('filepath')}")
    if input("Delete this song and its listening history? Type 'yes': ").strip().lower() != "yes":
        raise SystemExit("Nothing was deleted.")

    database.delete_song(song_id)
    conn = database.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    conn.close()
    print(f"Deleted song {song_id}. Total songs: {total}")


if __name__ == "__main__":
    main()
