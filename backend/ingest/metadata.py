"""Load metadata from acestep JSON files and match to MP3 files."""

import json
import os
import re
from pathlib import Path

import config


def normalize_filename(name: str) -> str:
    """Normalize a filename stem for matching purposes."""
    # Remove common video suffixes
    suffixes = [
        r"\(Official Lyric Video\)",
        r"\[Official Lyric Video\]",
        r"\(Official Audio\)",
        r"\[Official Audio\]",
        r"\(Lyrics\)",
        r"\[Lyrics\]",
        r"\(Lyrics \u2044 AMV\)",
        r"\[Lyrics x AMV\]",
        r"\(Visualizer \u2044 Lyric Video\)",
        r"\(Audio\)",
        r"\[AMV Edit\]",
        r"\(Music Video\)",
        r"\(Official Music Video\)",
        r"\(Official Video\)",
        r"\[CC\]",
        r" \[CC\]",
        r" \u2014 Slowed To Perfection",
        r" \u2014 Slowed",
        r"\(ft\.",
        r"\(feat\.",
        r" \u2013 ",
        r" - ",
    ]
    normalized = name
    for suffix in suffixes:
        normalized = re.sub(suffix, "", normalized, flags=re.IGNORECASE)
    # Remove special bracket characters
    normalized = re.sub(r"[❰❱⟨⟩<>]", "", normalized)
    # Remove non-breaking spaces and other whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Remove leading/trailing punctuation
    normalized = normalized.strip("-_. ")
    return normalized.lower()


def build_json_index(json_dir: str) -> dict:
    """Build a lookup from normalized filename to JSON data."""
    index = {}
    json_path = Path(json_dir)
    if not json_path.exists():
        return index

    for f in json_path.glob("*.json"):
        if f.name == "batch_manifest.json":
            continue
        stem = f.stem
        normalized = normalize_filename(stem)
        index[normalized] = str(f)
    return index


def load_acestep_json(json_file: str) -> dict | None:
    """Load and parse an acestep JSON file."""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        entry = data[0]
        return {
            "caption": entry.get("caption", ""),
            "lyrics": entry.get("lyrics", ""),
            "bpm": entry.get("bpm"),
            "duration": entry.get("duration"),
            "keyscale": entry.get("keyscale"),
            "timesignature": entry.get("timesignature"),
            "vocal_language": entry.get("vocal_language"),
        }
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def find_matching_json(mp3_filepath: str, json_index: dict) -> str | None:
    """Find the matching acestep JSON for an MP3 file."""
    stem = Path(mp3_filepath).stem
    # Remove .mp3 extension artifacts
    normalized = normalize_filename(stem)

    # Direct match
    if normalized in json_index:
        return json_index[normalized]

    # Try partial matching
    for key, value in json_index.items():
        if normalized in key or key in normalized:
            return value

    return None


def load_all_metadata() -> dict:
    """Load all acestep metadata indexed by MP3 stem."""
    json_index = build_json_index(config.ACESTEP_JSON_DIR)
    metadata = {}
    for key, json_file in json_index.items():
        data = load_acestep_json(json_file)
        if data:
            metadata[key] = data
    return metadata


def get_metadata_for_mp3(mp3_filepath: str, metadata: dict) -> dict | None:
    """Get acestep metadata for a specific MP3 file."""
    stem = Path(mp3_filepath).stem
    normalized = normalize_filename(stem)

    # Direct match
    if normalized in metadata:
        return metadata[normalized]

    # Partial match
    for key, data in metadata.items():
        if normalized in key or key in normalized:
            return data

    return None
