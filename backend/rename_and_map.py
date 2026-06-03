"""Match JSON metadata files to new MP3 filenames.

Usage:
    python rename_and_map.py match          # Dry run - shows matches only
    python rename_and_map.py match --apply  # Also renames JSON files to match

After matching, rebuild the DB:
    POST /api/ingest/run
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

# Force UTF-8 output for console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add backend to path so we can import ingest modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingest import metadata
import config


def find_all_mp3s(music_dir):
    """Recursively find all MP3 files."""
    mp3s = []
    music_path = Path(music_dir)
    if not music_path.exists():
        return mp3s
    for f in music_path.rglob("*.mp3"):
        mp3s.append(str(f))
    return sorted(mp3s)


def find_all_jsons(json_dir):
    """Find all acestep JSON files (excluding batch_manifest.json)."""
    jsons = []
    json_path = Path(json_dir)
    if not json_path.exists():
        return jsons
    for f in json_path.glob("*.json"):
        if f.name == "batch_manifest.json":
            continue
        jsons.append(str(f))
    return sorted(jsons)


def build_match_results(mp3s, jsons):
    """Match each JSON to the best MP3 by normalized filename."""
    # Build index of MP3s: normalized_stem -> list of (filepath, stem)
    mp3_index = {}
    for mp3_path in mp3s:
        stem = Path(mp3_path).stem
        normalized = metadata.normalize_filename(stem)
        mp3_index.setdefault(normalized, []).append((mp3_path, stem))

    matched = []
    ambiguous = []
    unmatched_jsons = []
    used_mp3s = set()

    for json_path in jsons:
        json_stem = Path(json_path).stem
        json_normalized = metadata.normalize_filename(json_stem)

        # Direct match
        if json_normalized in mp3_index:
            candidates = mp3_index[json_normalized]
            if len(candidates) == 1:
                mp3_path, mp3_stem = candidates[0]
                if mp3_path not in used_mp3s:
                    matched.append({
                        "json": json_path,
                        "mp3": mp3_path,
                        "json_stem": json_stem,
                        "mp3_stem": mp3_stem,
                        "normalized": json_normalized,
                    })
                    used_mp3s.add(mp3_path)
                    continue
                else:
                    # MP3 already claimed by another JSON
                    ambiguous.append({
                        "json": json_path,
                        "json_stem": json_stem,
                        "matches": [{"mp3": mp3_path, "mp3_stem": mp3_stem} for mp3_path, mp3_stem in candidates],
                    })
                    continue

        # Partial match
        best_match = None
        for key, candidates in mp3_index.items():
            if json_normalized in key or key in json_normalized:
                # Find an unused candidate
                for mp3_path, mp3_stem in candidates:
                    if mp3_path not in used_mp3s:
                        best_match = (mp3_path, mp3_stem)
                        break
                if best_match:
                    break

        if best_match:
            mp3_path, mp3_stem = best_match
            matched.append({
                "json": json_path,
                "mp3": mp3_path,
                "json_stem": json_stem,
                "mp3_stem": mp3_stem,
                "normalized": json_normalized,
            })
            used_mp3s.add(mp3_path)
        else:
            unmatched_jsons.append({
                "json": json_path,
                "json_stem": json_stem,
                "normalized": json_normalized,
            })

    # Find unmatched MP3s (not claimed by any JSON)
    all_matched_mp3s = {m["mp3"] for m in matched}
    unmatched_mp3s = []
    for mp3_path in mp3s:
        if mp3_path not in all_matched_mp3s:
            unmatched_mp3s.append({
                "mp3": mp3_path,
                "mp3_stem": Path(mp3_path).stem,
                "normalized": metadata.normalize_filename(Path(mp3_path).stem),
            })

    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched_jsons": unmatched_jsons,
        "unmatched_mp3s": unmatched_mp3s,
    }


def print_results(results):
    """Print a summary of the match results."""
    print("\n" + "=" * 70)
    print("MATCH RESULTS")
    print("=" * 70)
    print(f"  Matched:      {len(results['matched'])} JSONs -> MP3s")
    print(f"  Ambiguous:    {len(results['ambiguous'])} JSONs (multiple MP3 candidates)")
    print(f"  Unmatched:    {len(results['unmatched_jsons'])} JSONs (no MP3 match)")
    print(f"  Orphan MP3s:  {len(results['unmatched_mp3s'])} MP3s (no JSON match)")
    print("=" * 70)

    def safe_name(path_str):
        """Get filename, replacing unencodable chars."""
        try:
            name = os.path.basename(path_str)
            return name.encode("utf-8", "replace").decode("utf-8")
        except Exception:
            return "[unknown]"

    if results["ambiguous"]:
        print("\nAMBIGUOUS MATCHES:")
        for item in results["ambiguous"]:
            print(f"  JSON: {safe_name(item['json'])}")
            for m in item["matches"]:
                print(f"    -> {safe_name(m['mp3'])}")

    if results["unmatched_jsons"]:
        print(f"\nUNMATCHED JSONs ({len(results['unmatched_jsons'])}):")
        for item in results["unmatched_jsons"][:20]:
            print(f"  {safe_name(item['json'])}")
        if len(results["unmatched_jsons"]) > 20:
            print(f"  ... and {len(results['unmatched_jsons']) - 20} more")

    if results["unmatched_mp3s"]:
        print(f"\nORPHAN MP3s ({len(results['unmatched_mp3s'])}):")
        for item in results["unmatched_mp3s"][:20]:
            print(f"  {safe_name(item['mp3'])}")
        if len(results["unmatched_mp3s"]) > 20:
            print(f"  ... and {len(results['unmatched_mp3s']) - 20} more")


def apply_renames(results, json_dir):
    """Rename JSON files to match their matched MP3 names exactly."""
    renamed = 0
    skipped = 0
    errors = 0

    def safe_name(path_str):
        try:
            return os.path.basename(path_str).encode("utf-8", "replace").decode("utf-8")
        except Exception:
            return "[unknown]"

    for item in results["matched"]:
        json_path = item["json"]
        mp3_stem = item["mp3_stem"]

        # Use the MP3's stem directly as the JSON filename
        target_stem = mp3_stem
        target_path = os.path.join(json_dir, f"{target_stem}.json")

        # Skip if target already exists (would overwrite)
        if os.path.exists(target_path) and os.path.abspath(target_path) != os.path.abspath(json_path):
            print(f"  SKIP: {safe_name(json_path)} -> {safe_name(target_path)} (target exists)")
            skipped += 1
            continue

        try:
            if os.path.abspath(json_path) != os.path.abspath(target_path):
                os.rename(json_path, target_path)
                print(f"  RENAME: {safe_name(json_path)} -> {safe_name(target_path)}")
                renamed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR: {safe_name(json_path)} -> {safe_name(target_path)}: {e}")
            errors += 1

    print(f"\n  Renamed: {renamed} JSON files")
    print(f"  Skipped: {skipped} (no change or target exists)")
    if errors:
        print(f"  Errors: {errors}")


def main():
    parser = argparse.ArgumentParser(description="Match JSON metadata files to MP3 filenames")
    parser.add_argument("command", choices=["match"], help="Command to run")
    parser.add_argument("--apply", action="store_true", help="Apply renames to JSON files")
    args = parser.parse_args()

    mp3s = find_all_mp3s(config.MUSIC_DIR)
    jsons = find_all_jsons(config.ACESTEP_JSON_DIR)

    print(f"\nFound {len(mp3s)} MP3 files in {config.MUSIC_DIR}")
    print(f"Found {len(jsons)} JSON files in {config.ACESTEP_JSON_DIR}")

    results = build_match_results(mp3s, jsons)
    print_results(results)

    # Save results to file
    os.makedirs(config.DATA_DIR, exist_ok=True)
    output_path = os.path.join(config.DATA_DIR, "match_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")

    if args.apply and results["matched"]:
        print("\nApplying renames...")
        apply_renames(results, config.ACESTEP_JSON_DIR)
        print("\nDone. You can now rebuild the DB:")
        print("  POST /api/ingest/run")
    elif args.apply:
        print("\nNo matches to apply.")
    else:
        print("\nRun with --apply to rename JSON files to match MP3 names.")


if __name__ == "__main__":
    main()
