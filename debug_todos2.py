from pathlib import Path
import sys
import io
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

done = [
    'aldn - 2016 [Lyrics \u2044 AMV].json',
    'aldn - 2ppl [official music video w\u2044 lyrics].json',
    'Afinity & Lizzy Land - I\'ll Be Your Home (Lyrics).json',
    'aldn - do or die feat. carolesdaughter (lyric video).json'
]

raw = Path('data/todos.txt').read_bytes()
if raw.startswith(b'\xef\xbb\xbf'):
    raw = raw[3:]
content = raw.decode('utf-8')

lines = content.split('\n')

for line in lines[:10]:
    stripped = line.strip()
    match = re.match(r'^\d+\.\s+(.+\.json)$', stripped)
    if match:
        fname = match.group(1)
        print(f'Found: {repr(fname)}')
        if fname in done:
            print(f'  -> MATCH!')
        else:
            print(f'  -> no match')
