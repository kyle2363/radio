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

# Read as bytes to preserve exact encoding
raw = Path('data/todos.txt').read_bytes()
# Remove BOM if present
if raw.startswith(b'\xef\xbb\xbf'):
    raw = raw[3:]
content = raw.decode('utf-8')

lines = content.split('\n')
new_lines = []
removed = 0

for line in lines:
    stripped = line.strip()
    if not stripped:
        new_lines.append(line)
        continue
    
    # Check if this is a numbered entry
    match = re.match(r'^\d+\.\s+(.+\.json)$', stripped)
    if match:
        fname = match.group(1)
        if fname in done:
            removed += 1
            continue
    
    new_lines.append(line)

output = '\n'.join(new_lines)
Path('data/todos.txt').write_bytes(output.encode('utf-8'))

print(f'Removed {removed} entries')
remaining = sum(1 for l in new_lines if re.match(r'^\d+\.', l.strip()))
print(f'Remaining entries: {remaining}')
