import json
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

d = Path('docs/understand_new')
no_lyrics = []

for f in sorted(d.glob('*.json')):
    if f.name == 'batch_manifest.json':
        continue
    data = json.load(open(f, encoding='utf-8'))
    lyr = data[0].get('lyrics', '').strip() if isinstance(data, list) else ''
    if lyr == 'NOT FOUND':
        continue
    if not lyr or len(lyr) < 20:
        no_lyrics.append(f.name)

lines = [f'{i+1}. {name}' for i, name in enumerate(no_lyrics)]
Path('data/todos.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(f'Total TODO: {len(no_lyrics)}')
