import sqlite3, json, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect(r'C:\Users\kyleh\Desktop\data\projects\radio\data\radio.db')
c = conn.cursor()

c.execute('SELECT cluster_id, COUNT(*) FROM songs WHERE cluster_id >= 0 GROUP BY cluster_id')
total = sum(r[1] for r in c.fetchall())
print(f'Total clustered songs: {total}\n')

for cid in range(18):
    c.execute('''
        SELECT title, artist, energy, valence, bpm, play_count, complete_count,
               emotional_tags, genre_tags, instrument_tags, ai_description
        FROM songs WHERE cluster_id = ?
        ORDER BY play_count DESC, title
        LIMIT 20
    ''', (cid,))
    rows = c.fetchall()
    c.execute('SELECT COUNT(*) FROM songs WHERE cluster_id = ?', (cid,))
    cluster_total = c.fetchone()[0]
    print(f'Cluster {cid} ({cluster_total} songs)')
    print('-' * 100)
    for title, artist, energy, valence, bpm, plays, complete, emo, genre, inst, desc in rows:
        emo_str = json.dumps(json.loads(emo) if isinstance(emo, str) else emo)
        genre_str = json.dumps(json.loads(genre) if isinstance(genre, str) else genre)
        inst_str = json.dumps(json.loads(inst) if isinstance(inst, str) else inst)
        plays_str = f'{plays}p' if plays else '-'
        print(f'  {plays_str:>4} | E={energy:.2f} V={valence:.2f} {bpm:>4} | {artist} - {title}')
        print(f'         {emo_str}')
        print(f'         {genre_str}')
        print(f'         {inst_str}')
        print()
    if cluster_total > 20:
        print(f'  ... and {cluster_total - 20} more\n')
    print()

conn.close()
