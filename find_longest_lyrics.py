import json, glob, os, sys
sys.stdout.reconfigure(encoding='utf-8')

json_dir = r'C:\Users\kyleh\Desktop\data\projects\radio\docs\understand'
json_files = glob.glob(os.path.join(json_dir, '*.json'))

max_len = 0
max_file = ''
for jf in json_files:
    try:
        with open(jf, encoding='utf-8', errors='replace') as f:
            data = json.load(f)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            lyrics = data[0].get('lyrics', '')
            if lyrics and len(lyrics) > max_len:
                max_len = len(lyrics)
                max_file = os.path.basename(jf)
    except:
        pass

with open(os.path.join(json_dir, max_file), encoding='utf-8', errors='replace') as f:
    data = json.load(f)
    lyrics = data[0].get('lyrics', '')
    wc = len(lyrics.split())

print(f"Longest: {max_file}")
print(f"  {max_len} chars, {wc} words")
print(f"  First 200 chars: {lyrics[:200]}")
print(f"  Last 200 chars: {lyrics[-200:]}")
