import sqlite3, json, chromadb
import sys
sys.stdout.reconfigure(encoding='utf-8')

# --- DB queries ---
conn = sqlite3.connect(r'C:\Users\kyleh\Desktop\data\projects\radio\data\radio.db')
c = conn.cursor()

# Summary
c.execute('SELECT COUNT(*) FROM songs WHERE cluster_id >= 0')
print(f'Songs with cluster_id >= 0: {c.fetchone()[0]}')
print()

c.execute('SELECT cluster_id, COUNT(*) as count, AVG(energy), AVG(valence), AVG(bpm), MIN(energy), MAX(energy), MIN(valence), MAX(valence) FROM songs WHERE cluster_id >= 0 GROUP BY cluster_id ORDER BY cluster_id')
print('Cluster Summary:')
print(f'{"Cluster":<10}{"Count":<8}{"Avg E":<10}{"Avg V":<10}{"Avg BPM":<10}{"E Range":<12}{"V Range":<12}')
print('-' * 72)
for row in c.fetchall():
    cid, count, ae, av, abpm, emin, emax, vmin, vmax = row
    print(f'{cid:<10}{count:<8}{ae:<10.3f}{av:<10.3f}{abpm:<10.0f}{emin:.2f}-{emax:.2f}{vmin:.2f}-{vmax:.2f}')

print()
print('=' * 80)
print('TAG BREAKDOWN PER CLUSTER')
print('=' * 80)

for cid in range(18):
    c.execute('SELECT emotional_tags FROM songs WHERE cluster_id = ? AND emotional_tags IS NOT NULL', (cid,))
    tag_counts = {}
    for (emo_json,) in c.fetchall():
        for t in (json.loads(emo_json) if isinstance(emo_json, str) else emo_json):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_emo = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]

    c.execute('SELECT genre_tags FROM songs WHERE cluster_id = ? AND genre_tags IS NOT NULL', (cid,))
    tag_counts = {}
    for (g,) in c.fetchall():
        for t in (json.loads(g) if isinstance(g, str) else g):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_genre = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]

    c.execute('SELECT instrument_tags FROM songs WHERE cluster_id = ? AND instrument_tags IS NOT NULL', (cid,))
    tag_counts = {}
    for (i,) in c.fetchall():
        for t in (json.loads(i) if isinstance(i, str) else i):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_inst = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]

    print(f'\\nCluster {cid}:')
    print(f'  Emotional: {top_emo}')
    print(f'  Genre:     {top_genre}')
    print(f'  Instrument:{top_inst}')

print()
print('=' * 80)
print('TOP SONGS PER CLUSTER (by play count)')
print('=' * 80)

for cid in range(18):
    c.execute('''
        SELECT title, artist, energy, valence, bpm, play_count, emotional_tags, genre_tags
        FROM songs WHERE cluster_id = ? AND play_count > 0
        ORDER BY play_count DESC LIMIT 3
    ''', (cid,))
    rows = c.fetchall()
    if rows:
        print(f'\\nCluster {cid}:')
        for title, artist, energy, valence, bpm, plays, emo, genre in rows:
            emo_str = json.dumps(json.loads(emo) if isinstance(emo, str) else emo)
            genre_str = json.dumps(json.loads(genre) if isinstance(genre, str) else genre)
            print(f'  [{plays:2d}x] {artist} - {title}')
            print(f'       energy={energy:.2f} valence={valence:.2f} bpm={bpm}')
            print(f'       {emo_str} | {genre_str}')
    else:
        print(f'\\nCluster {cid}: (no plays yet)')

conn.close()

# --- ChromaDB centroids ---
print()
print('=' * 80)
print('CHROMADB CENTROID DATA')
print('=' * 80)

client = chromadb.PersistentClient(path=r'C:\Users\kyleh\Desktop\data\projects\radio\data\chroma')
coll = client.get_collection('cluster_centroids')
results = coll.get()
for id_, meta in zip(results['ids'], results['metadatas']):
    print(f'Centroid {id_}: energy={meta.get("energy","?")}, valence={meta.get("valence","?")}, bpm={meta.get("bpm","?")}')
    print(f'  tags: {meta.get("tags","")}')
