"""Load metadata from acestep JSON files and match to MP3 files."""

import json
import os
import re
from pathlib import Path

import config
from ingest.scanner import compute_file_hash


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


def metadata_key(filepath: str) -> str:
    """Return a stable metadata key for one audio path."""
    stem = normalize_filename(Path(filepath).stem)
    return f"{stem}--{compute_file_hash(filepath)}"


def metadata_path(filepath: str, json_dir: str | None = None) -> str:
    """Return the flat JSON path uniquely associated with an audio path."""
    directory = json_dir if json_dir is not None else config.ACESTEP_JSON_DIR
    stem = Path(filepath).stem
    return str(Path(directory) / f"{stem}--{compute_file_hash(filepath)}.json")


def _append_index_value(index: dict, key: str, value: object) -> None:
    """Keep collisions so callers can reject ambiguous legacy matches."""
    current = index.get(key)
    if current is None:
        index[key] = value
    elif isinstance(current, list):
        current.append(value)
    else:
        index[key] = [current, value]


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
        hashed = re.fullmatch(r"(.+)--([0-9a-f]{64})", stem, flags=re.IGNORECASE)
        if hashed:
            key = f"{normalize_filename(hashed.group(1))}--{hashed.group(2).lower()}"
        else:
            key = normalize_filename(stem)
        _append_index_value(index, key, str(f))
    return index


def load_acestep_json(json_file: str) -> dict | None:
    """Load and parse an acestep JSON file."""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data or not isinstance(data, list) or not isinstance(data[0], dict):
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
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


def _find_best_match(normalized: str, items: dict) -> tuple[object | None, float]:
    """Find the best matching value for *normalized* among dict keys.

    Returns ``(value, score)``.  The dict maps *key → value* where *key*
    has already been normalised.  Matching strategy (in priority order):

    1. Exact key match
    2. Substring match (similarity ≥ 0.8)
    3. Token overlap (Jaccard ≥ 0.8)
    """
    # Direct match is safe only when the normalized name is unique.
    if normalized in items:
        value = items[normalized]
        if isinstance(value, list):
            return (value[0], 1.0) if len(value) == 1 else (None, 1.0)
        return value, 1.0

    candidates = []
    for key, value in items.items():
        # Path-hashed entries are exact-only. Fuzzy legacy matching must not
        # accidentally select another same-basename track's metadata.
        if re.search(r"--[0-9a-f]{64}$", key, flags=re.IGNORECASE):
            continue
        score = 0.0
        if len(normalized) >= 5 and normalized in key:
            score = max(score, len(normalized) / len(key))
        if len(key) >= 5 and key in normalized:
            score = max(score, len(key) / len(normalized))
        overlap = _token_overlap(normalized, key)
        if overlap >= 0.8:
            score = max(score, overlap)
        if score >= 0.8:
            values = value if isinstance(value, list) else [value]
            candidates.extend((score, candidate) for candidate in values)

    if not candidates:
        return None, 0.0
    best_score = max(score for score, _ in candidates)
    best_values = [value for score, value in candidates if score == best_score]
    if len(best_values) != 1:
        return None, best_score
    return best_values[0], best_score


def find_matching_json(mp3_filepath: str, json_index: dict) -> str | None:
    """Find the matching acestep JSON file path for an MP3 file."""
    exact_key = metadata_key(mp3_filepath)
    if exact_key in json_index:
        value = json_index[exact_key]
        return value[0] if isinstance(value, list) and len(value) == 1 else (value if not isinstance(value, list) else None)
    normalized = normalize_filename(Path(mp3_filepath).stem)
    legacy_index = {key: value for key, value in json_index.items()
                    if not re.search(r"--[0-9a-f]{64}$", key, flags=re.IGNORECASE)}
    value, _ = _find_best_match(normalized, legacy_index)
    return value


def load_all_metadata() -> dict:
    """Load all acestep metadata indexed by MP3 stem."""
    json_index = build_json_index(config.ACESTEP_JSON_DIR)
    metadata = {}
    for key, json_files in json_index.items():
        for json_file in json_files if isinstance(json_files, list) else [json_files]:
            data = load_acestep_json(json_file)
            if data:
                _append_index_value(metadata, key, data)
    return metadata


def get_metadata_for_mp3(mp3_filepath: str, metadata: dict) -> dict | None:
    """Get acestep metadata for a specific MP3 file."""
    exact_key = metadata_key(mp3_filepath)
    if exact_key in metadata:
        value = metadata[exact_key]
        return value[0] if isinstance(value, list) and len(value) == 1 else (value if not isinstance(value, list) else None)
    normalized = normalize_filename(Path(mp3_filepath).stem)
    legacy_index = {key: value for key, value in metadata.items()
                    if not re.search(r"--[0-9a-f]{64}$", key, flags=re.IGNORECASE)}
    value, _ = _find_best_match(normalized, legacy_index)
    return value if isinstance(value, dict) else None
