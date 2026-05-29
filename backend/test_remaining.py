"""Check remaining described songs."""
from db import database
database.init_db()

songs = database.get_by_ingest_phase("described")
print(f"Remaining described: {len(songs)}")
for s in songs:
    print(f"  ID={s['id']} artist={s.get('artist')} title={s.get('title')} energy={s.get('energy')} valence={s.get('valence')}")

# Check ingest status
status = database.get_ingest_status()
print(f"\nIngest status: {status}")
