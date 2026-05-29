import sys
sys.path.insert(0, "C:/Users/kyleh/Desktop/data/projects/radio/backend")
from ingest import metadata

# Test loading a JSON with lyrics
json_file = r"C:\Users\kyleh\Desktop\data\projects\radio\docs\understand\16 - Make no sense [Music Video].json"
result = metadata.load_acestep_json(json_file)
print(f"load_acestep_json result: {result is not None}")
if result:
    cap = result.get("caption", "")
    lyr = result.get("lyrics", "")
    print(f"  caption: {cap[:60]}...")
    print(f"  lyrics: {lyr[:60]}...")
    print(f"  lyrics type: {type(lyr).__name__}")
    print(f"  lyrics bool: {bool(lyr)}")

# Test matching
mp3_path = r"C:\Users\kyleh\Desktop\data\projects\radio\audio_files\new 9 mp3\16 - Make no sense [Music Video].mp3"
all_meta = metadata.load_all_metadata()
print(f"\nLoaded {len(all_meta)} metadata entries")
matched = metadata.get_metadata_for_mp3(mp3_path, all_meta)
print(f"Matched: {matched is not None}")
if matched:
    print(f"  lyrics: {matched.get('lyrics', '')[:60]}...")
else:
    print("  NO MATCH - checking normalization")
    stem = "16 - Make no sense [Music Video]"
    norm = metadata.normalize_filename(stem)
    print(f"  normalized stem: {norm}")
    matches = [k for k in all_meta.keys() if norm in k or k in norm]
    print(f"  partial matches: {matches[:5]}")
