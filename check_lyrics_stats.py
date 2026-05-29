import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from db import database

database.init_db()
conn = database.get_connection()
rows = conn.execute("SELECT lyrics FROM songs WHERE lyrics IS NOT NULL AND TRIM(lyrics) != '' AND TRIM(lyrics) != 'TODO'").fetchall()
print(f"Songs with lyrics in DB: {len(rows)}")
if rows:
    char_counts = [len(r[0]) for r in rows if r[0]]
    word_counts = [len(r[0].split()) for r in rows if r[0]]
    print(f"Chars - min:{min(char_counts)} max:{max(char_counts)} avg:{sum(char_counts)/len(char_counts):.0f}")
    print(f"Words - min:{min(word_counts)} max:{max(word_counts)} avg:{sum(word_counts)/len(word_counts):.0f}")
    print(f"300 words ~ {300*5} chars (5 chars/word est)")
else:
    print("No lyrics in DB yet")
conn.close()
