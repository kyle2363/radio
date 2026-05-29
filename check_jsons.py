import json, glob, os, sys
sys.stdout.reconfigure(encoding='utf-8')

json_dir = r"C:\Users\kyleh\Desktop\data\projects\radio\docs\understand"
json_files = glob.glob(os.path.join(json_dir, "*.json"))
print(f"Total JSON files: {len(json_files)}")

valid = 0
invalid = 0
empty_list = 0
not_list = 0
missing_lyrics = 0
empty_lyrics = 0
todo_lyrics = 0
has_lyrics = 0

for jf in json_files:
    name = os.path.basename(jf)
    try:
        with open(jf, encoding='utf-8', errors='replace') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        invalid += 1
        print(f"  INVALID JSON: {name} - {e}")
        continue

    if not isinstance(data, list):
        not_list += 1
        print(f"  NOT A LIST: {name} - type={type(data).__name__}")
        continue

    if len(data) == 0:
        empty_list += 1
        print(f"  EMPTY LIST: {name}")
        continue

    entry = data[0]
    if not isinstance(entry, dict):
        not_list += 1
        print(f"  NOT A DICT: {name} - type={type(entry).__name__}")
        continue

    lyrics = entry.get("lyrics")
    if lyrics is None:
        missing_lyrics += 1
    elif isinstance(lyrics, str):
        stripped = lyrics.strip()
        if stripped == "":
            empty_lyrics += 1
        elif stripped == "TODO":
            todo_lyrics += 1
        else:
            has_lyrics += 1
    else:
        print(f"  LYRICS BAD TYPE: {name} - type={type(lyrics).__name__}, val={repr(lyrics)[:50]}")

print(f"\nValid: {valid + invalid if False else 'N/A'}")
print(f"Invalid JSON: {invalid}")
print(f"Not a list: {not_list}")
print(f"Empty list: {empty_list}")
print(f"Missing lyrics: {missing_lyrics}")
print(f"Empty lyrics: {empty_lyrics}")
print(f"TODO lyrics: {todo_lyrics}")
print(f"Has lyrics: {has_lyrics}")
