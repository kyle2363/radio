import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'backend')
from db import database

conn = database.get_connection()
rows = conn.execute('''
    SELECT cluster_id, artist, title, energy, valence, bpm, ai_description
    FROM songs
    WHERE cluster_id >= 0
    ORDER BY cluster_id, energy DESC, title
''').fetchall()

for cid in range(18):
    cluster_rows = [r for r in rows if r['cluster_id'] == cid]
    count = len(cluster_rows)
    if count == 0:
        continue
    print(f'=== Cluster {cid} ({count} songs) ===')
    for r in cluster_rows[:10]:
        artist = (r['artist'] or '?')[:25]
        title = (r['title'] or '?')[:30]
        desc = (r['ai_description'] or '')[:120]
        energy = r['energy']
        valence = r['valence']
        print(f'  [{energy:.1f}/{valence:.1f}] {artist} - {title}')
        if desc:
            print(f'    {desc}')
    print()
