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
        "\\(Lyrics \u2044 AMV\\)",
        r"\[Lyrics x AMV\]",
        "\\(Visualizer \u2044 Lyric Video\\)",
        r"\(Audio\)",
        r"\[AMV Edit\]",
        r"\(Music Video\)",
        r"\(Official Music Video\)",
        r"\(Official Video\)",
        r"\[CC\]",
        r" \[CC\]",
        " \u2014 Slowed To Perfection",
        " \u2014 Slowed",
        r"\(ft\.",
        r"\(feat\.",
        " \u2013 ",
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
            "dj_effect_eligible": entry.get("dj_effect_eligible"),
        }
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def _token_overlap(a: str, b: str) -> float:
    """Return Jaccard similarity of word tokens between two strings."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _find_best_match(normalized: str, items: dict) -> tuple[str | None, float]:
    """Find the best matching value for *normalized* among dict keys.

    Returns ``(value, score)``.  The dict maps *key → value* where *key*
    has already been normalised.  Matching strategy (in priority order):

    1. Exact key match
    2. Substring match (longest match wins)
    3. Token overlap (Jaccard ≥ 0.6)
    """
    # Direct match
    if normalized in items:
        return items[normalized], 1.0

    best_value = None
    best_score = 0.0
    for key, value in items.items():
        # Require minimum substring length to avoid false positives
        if len(normalized) >= 5 and normalized in key:
            score = len(normalized) / len(key)
            if score > best_score:
                best_score = score
                best_value = value
        if len(key) >= 5 and key in normalized:
            score = len(key) / len(normalized)
            if score > best_score:
                best_score = score
                best_value = value
        # Token overlap as a tiebreaker for ambiguous cases
        if best_score < 0.5:
            overlap = _token_overlap(normalized, key)
            if overlap >= 0.6 and overlap > best_score:
                best_score = overlap
                best_value = value

    return best_value, best_score


def find_matching_json(mp3_filepath: str, json_index: dict) -> str | None:
    """Find the matching acestep JSON file path for an MP3 file."""
    stem = Path(mp3_filepath).stem
    normalized = normalize_filename(stem)
    value, _ = _find_best_match(normalized, json_index)
    return value


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
    value, _ = _find_best_match(normalized, metadata)
    return value
