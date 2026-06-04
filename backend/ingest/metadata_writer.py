"""Write metadata back to source files (MP3 ID3 tags + acestep JSONs).

This module writes user edits to the actual files on disk so that
metadata changes survive database deletion. When the ingest pipeline
is re-run, it will reload these edits from the source files.

- ID3 tags (artist, title, album, genre) are written to MP3 files
- Acestep JSON fields (bpm, duration, keyscale, lyrics, caption, etc.)
  are written to the corresponding JSON file in docs/understand/
- MP3 files can be renamed to match new artist/title for pipeline compatibility
"""

import json
import logging
import os
import re
import shutil
from pathlib import Path

import config
from ingest.metadata import find_matching_json, build_json_index, normalize_filename

logger = logging.getLogger(__name__)


def write_id3_tags(filepath, artist=None, title=None, album=None, genre=None):
    """Write ID3 tags to an MP3 file.

    Uses mutagen to add/update TIT2 (title), TPE1 (artist),
    TALB (album), and TCON (genre) frames. Creates the ID3 header
    if it doesn't exist.

    Returns dict with status per field: {field: "ok" | "error" | "skipped"}
    """
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON

    result = {}
    try:
        audio = MutagenFile(filepath, easy=True)
        if audio is None:
            logger.warning("Could not open MP3 file for ID3 writing: %s", filepath)
            for field in ["artist", "title", "album", "genre"]:
                if field in [artist, title, album, genre] and [artist, title, album, genre][["artist", "title", "album", "genre"].index(field)]:
                    result[field] = "error"
                else:
                    result[field] = "skipped"
            return result

        # Ensure ID3 tag frame exists
        if not hasattr(audio, "tags") or audio.tags is None:
            audio.add_tags()

        if artist is not None and artist.strip():
            audio.tags["artist"] = artist.strip()
            result["artist"] = "ok"
        else:
            result["artist"] = "skipped"

        if title is not None and title.strip():
            audio.tags["title"] = title.strip()
            result["title"] = "ok"
        else:
            result["title"] = "skipped"

        if album is not None and album.strip():
            audio.tags["album"] = album.strip()
            result["album"] = "ok"
        else:
            result["album"] = "skipped"

        if genre is not None and genre.strip():
            audio.tags["genre"] = genre.strip()
            result["genre"] = "ok"
        else:
            result["genre"] = "skipped"

        audio.save()
    except Exception as e:
        logger.error("Failed to write ID3 tags for %s: %s", filepath, e)
        for field in ["artist", "title", "album", "genre"]:
            result[field] = "error"

    return result


def _find_acestep_json_path(mp3_filepath, json_index=None):
    """Find the acestep JSON file path for an MP3 file."""
    if json_index is None:
        json_index = build_json_index(config.ACESTEP_JSON_DIR)
    return find_matching_json(mp3_filepath, json_index)


def write_acestep_json(mp3_filepath, **fields):
    """Write metadata fields to the acestep JSON file matching this MP3.

    Updates the JSON array entry in-place, preserving acestep-specific
    fields like seed, lm_seed, solver, etc.

    Supports fields: caption, lyrics, bpm, duration, keyscale,
    timesignature, vocal_language

    Returns dict: {json_file: "ok" | "error", fields_updated: [...], fields_error: [...]}
    """
    json_file = _find_acestep_json_path(mp3_filepath)
    if not json_file:
        return {
            "json_file": None,
            "status": "no_match",
            "fields_updated": [],
            "fields_error": [],
            "reason": "No acestep JSON found for this MP3",
        }

    valid_fields = {"caption", "lyrics", "bpm", "duration", "keyscale", "timesignature", "vocal_language"}
    fields_to_write = {k: v for k, v in fields.items() if k in valid_fields}

    if not fields_to_write:
        return {
            "json_file": json_file,
            "status": "no_fields",
            "fields_updated": [],
            "fields_error": [],
        }

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data or not isinstance(data, list) or len(data) == 0:
            return {
                "json_file": json_file,
                "status": "invalid_format",
                "fields_updated": [],
                "fields_error": [],
                "reason": "ACEstep JSON has unexpected format",
            }

        entry = data[0]
        fields_updated = []
        fields_error = []

        for key, value in fields_to_write.items():
            try:
                if value is None:
                    continue
                # Convert to appropriate type
                if key in ("bpm",) and isinstance(value, str):
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        pass
                elif key in ("duration",) and isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        pass
                elif key in ("bpm", "duration") and not isinstance(value, (int, float)):
                    continue

                entry[key] = value
                fields_updated.append(key)
            except Exception as e:
                logger.error("Failed to write field %s to %s: %s", key, json_file, e)
                fields_error.append(key)

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        status = "ok" if fields_updated else "no_changes"
        return {
            "json_file": json_file,
            "status": status,
            "fields_updated": fields_updated,
            "fields_error": fields_error,
        }

    except Exception as e:
        logger.error("Failed to write acestep JSON %s: %s", json_file, e)
        return {
            "json_file": json_file,
            "status": "error",
            "fields_updated": [],
            "fields_error": list(fields_to_write.keys()),
            "reason": str(e),
        }


def build_filename(artist, title):
    """Build a canonical filename from artist and title.

    Format: 'Artist - Title.mp3'
    Sanitizes the string to be filesystem-safe.
    """
    parts = []
    if artist:
        parts.append(str(artist).strip().replace("/", "\\").strip())
    if title:
        parts.append(str(title).strip().replace("/", "\\").strip())
    base = " - ".join(parts) if parts else "Unknown"
    # Remove characters invalid on Windows
    base = re.sub(r'[<>:"|?*]', "", base)
    base = base.strip(" .")
    if not base:
        base = "Unknown"
    return base + ".mp3"


def rename_song_files(old_filepath, new_artist=None, new_title=None):
    """Rename the MP3 file and corresponding acestep JSON to match new artist/title.

    Returns dict with:
      - mp3: {"old_path", "new_path", "status": "ok"|"error"|"skipped", "reason"}
      - json: {"old_path", "new_path", "status": "ok"|"error"|"skipped", "reason"}
      - normalized_name: the normalized filename stem used for JSON matching
    """
    result = {
        "mp3": {"old_path": old_filepath, "status": "skipped"},
        "json": {"status": "skipped"},
        "normalized_name": None,
    }

    if not new_artist and not new_title:
        return result

    new_artist = (new_artist or "").strip()
    new_title = (new_title or "").strip()

    # Build new filename
    new_filename = build_filename(new_artist, new_title)
    new_stem = Path(new_filename).stem
    result["normalized_name"] = normalize_filename(new_stem)

    # Rename MP3 file
    new_dir = os.path.dirname(old_filepath)
    new_filepath = os.path.join(new_dir, new_filename)

    if not os.path.exists(old_filepath):
        result["mp3"]["status"] = "error"
        result["mp3"]["reason"] = f"File not found: {old_filepath}"
        return result

    if os.path.abspath(old_filepath) == os.path.abspath(new_filepath):
        result["mp3"]["status"] = "skipped"
        result["mp3"]["reason"] = "Filename unchanged"
    else:
        try:
            if os.path.exists(new_filepath):
                # Avoid collision - append a number
                counter = 1
                while os.path.exists(new_filepath):
                    base = f"{new_stem} ({counter})"
                    new_filename = base + ".mp3"
                    new_filepath = os.path.join(new_dir, new_filename)
                    counter += 1

            shutil.move(old_filepath, new_filepath)
            result["mp3"]["new_path"] = new_filepath
            result["mp3"]["status"] = "ok"
            logger.info("Renamed MP3: %s -> %s", old_filepath, new_filepath)
        except Exception as e:
            result["mp3"]["status"] = "error"
            result["mp3"]["reason"] = str(e)
            return result

    # Rename acestep JSON to match new filename
    old_json = _find_acestep_json_path(old_filepath)
    if old_json:
        result["json"]["old_path"] = old_json
        new_json_path = os.path.join(os.path.dirname(old_json), new_filename.replace(".mp3", ".json"))

        if os.path.abspath(old_json) == os.path.abspath(new_json_path):
            result["json"]["status"] = "skipped"
            result["json"]["reason"] = "Filename unchanged"
        else:
            try:
                if os.path.exists(new_json_path):
                    counter = 1
                    while os.path.exists(new_json_path):
                        base = f"{new_stem} ({counter})"
                        new_json_path = os.path.join(os.path.dirname(old_json), base + ".json")
                        counter += 1

                shutil.move(old_json, new_json_path)
                result["json"]["new_path"] = new_json_path
                result["json"]["status"] = "ok"
                logger.info("Renamed acestep JSON: %s -> %s", old_json, new_json_path)
            except Exception as e:
                result["json"]["status"] = "error"
                result["json"]["reason"] = str(e)
                logger.error("Failed to rename acestep JSON: %s", e)

    return result


def update_metadata_for_song(song_id):
    """Read the updated song from DB and re-write all metadata to source files.

    This is called after the DB has been updated to ensure the source files
    match the current database state. Useful after re-running the ingest pipeline.

    Returns dict with write results.
    """
    from db import database

    song = database.get_song(song_id)
    if not song:
        return {"error": "Song not found"}

    filepath = song.get("filepath")
    if not filepath or not os.path.exists(filepath):
        return {"error": f"MP3 file not found: {filepath}"}

    # Write ID3 tags from DB fields
    id3_result = write_id3_tags(
        filepath,
        artist=song.get("artist"),
        title=song.get("title"),
        album=song.get("album"),
    )

    # Write acestep JSON from DB fields
    ace_result = write_acestep_json(
        filepath,
        caption=song.get("ai_description"),
        lyrics=song.get("lyrics"),
        bpm=song.get("bpm"),
        duration=song.get("duration_secs"),
        keyscale=song.get("keyscale"),
        timesignature=song.get("timesignature"),
        vocal_language=song.get("vocal_language"),
    )

    return {
        "id3": id3_result,
        "acestep": ace_result,
    }
