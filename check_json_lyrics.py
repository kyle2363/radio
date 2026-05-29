import json, os, glob, sys

sys.stdout.reconfigure(encoding='utf-8')

understand_dir = r"C:\Users\kyleh\Desktop\data\projects\radio\docs\understand"
json_files = glob.glob(os.path.join(understand_dir, "*.json"))
print(f"Total JSON files: {len(json_files)}")

def get_lyrics(data):
    if isinstance(data, dict):
        return data.get("lyrics")
    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0].get("lyrics")
    return None

lyrics_counts = {"null": 0, "empty": 0, "todo": 0, "has": 0}
skipped = 0
for jf in json_files:
    try:
        with open(jf, encoding='utf-8', errors='replace') as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception):
        skipped += 1
        continue
    lyrics = get_lyrics(data)
    if lyrics is None:
        lyrics_counts["null"] += 1
    elif isinstance(lyrics, str) and lyrics.strip() == "":
        lyrics_counts["empty"] += 1
    elif isinstance(lyrics, str) and lyrics.strip() == "TODO":
        lyrics_counts["todo"] += 1
    elif isinstance(lyrics, str) and len(lyrics.strip()) > 0:
        lyrics_counts["has"] += 1

print(f"Valid JSON: {len(json_files) - skipped}")
print(f"Skipped (corrupt): {skipped}")
print(f"\nLyrics counts:")
for k, v in lyrics_counts.items():
    print(f"  {k}: {v}")
