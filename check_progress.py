import sys
sys.path.insert(0, r'C:\Users\kyleh\Desktop\data\projects\radio\backend')
from db import database
database.init_db()
conn = database.get_connection()

rows = conn.execute('SELECT ingest_phase, COUNT(*) FROM songs GROUP BY ingest_phase').fetchall()
for r in rows:
    print(f'{r[0]}: {r[1]}')

lyrics = conn.execute("SELECT COUNT(*) FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' AND lyrics != 'TODO'").fetchone()[0]
total = conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0]
print(f'Lyrics: {lyrics}/{total}')

# Check songs with acestep captions
captions = conn.execute("SELECT COUNT(*) FROM songs WHERE ai_description IS NOT NULL AND ai_description != ''").fetchone()[0]
print(f'With captions: {captions}/{total}')

conn.close()
