"""Walk the music directory, find MP3 files, and deduplicate."""

import hashlib
import os
from pathlib import Path


AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


def parse_filename(filepath: str) -> dict:
    """Extract artist and title from filepath/filename.

    Tries patterns:
    - 'Artist - Title.ext' (most common)
    - Just the filename without extension as title
    """
    import os
    fname = os.path.basename(filepath)
    if fname.lower().endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac")):
        fname = fname[:fname.rindex(".")]

    result = {"artist": None, "title": None}

    # Try 'Artist - Title' pattern (split on last ' - ')
    if " - " in fname:
        idx = fname.rindex(" - ")
        result["artist"] = fname[:idx].strip()
        result["title"] = fname[idx + 3:].strip()

    if not result["title"]:
        result["title"] = fname

    return result


def compute_file_hash(filepath: str) -> str:
    """Compute SHA256 hash of the file path string (not content).
    Normalizes casing to prevent C: vs c: duplicates.
    """
    normalized = os.path.normpath(filepath).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def find_audio_files(folder: str) -> list[str]:
    """Recursively find all audio files in folder."""
    files = []
    folder = Path(folder)
    if not folder.exists():
        return files
    for root, _dirs, filenames in os.walk(folder):
        for fname in filenames:
            if Path(fname).suffix.lower() in AUDIO_EXTS:
                files.append(os.path.join(root, fname))
    files.sort()
    return files


def deduplicate(files: list[str]) -> dict:
    """Deduplicate by SHA256 of filepath. Keep first occurrence."""
    seen = {}
    duplicates = []
    unique = []
    for f in files:
        h = compute_file_hash(f)
        if h in seen:
            duplicates.append((f, seen[h]))
        else:
            seen[h] = f
            unique.append(f)
    return {"unique": unique, "duplicates": duplicates}


def scan_music_dir(music_dir: str) -> dict:
    """Scan music directory and return stats."""
    all_files = find_audio_files(music_dir)
    dedup = deduplicate(all_files)
    return {
        "total_files": len(all_files),
        "unique_files": len(dedup["unique"]),
        "duplicates": len(dedup["duplicates"]),
        "duplicate_details": [
            {"file": d[0], "kept": d[1]} for d in dedup["duplicates"]
        ],
    }
