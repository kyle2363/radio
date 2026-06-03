from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

content = Path('data/todos.txt').read_text(encoding='utf-8')
lines = content.split('\n')
for i, line in enumerate(lines[:10]):
    print(f'{i}: {repr(line)}')
