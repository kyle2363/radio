"""ID3 tag extraction and album art extraction for MP3 files."""

import os
import mutagen
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC


def extract_tags(filepath):
    """Extract ID3 tags from an MP3 file.

    Returns dict with artist, title, album, genre, duration_secs.
    Falls back to filename parsing if tags are missing.
    """
    result = {
        "artist": None,
        "title": None,
        "album": None,
        "genre": None,
        "duration_secs": None,
    }

    try:
        audio = MP3(filepath)
        result["duration_secs"] = round(audio.info.length, 2)
    except Exception as e:
        import logging; logging.getLogger(__name__).debug("Could not parse MP3 duration: %s", e)

    try:
        tags = ID3(filepath)
        # Try common frame IDs
        for frame_id, key in [
            ("TPE1", "artist"),
            ("TIT2", "title"),
            ("TALB", "album"),
            ("TCON", "genre"),
        ]:
            if frame_id in tags:
                val = tags[frame_id]
                if hasattr(val, "text") and val.text:
                    result[key] = str(val.text[0])
    except Exception as e:
        import logging; logging.getLogger(__name__).debug("Could not parse ID3 tags: %s", e)

    return result


def extract_album_art(filepath, art_dir, song_id):
    """Extract APIC album art from ID3 tags and save to art_dir.

    Returns relative path like "art/123.jpg" or None if no art found.
    """
    try:
        tags = ID3(filepath)
        for tag in tags.values():
            if isinstance(tag, APIC):
                ext = "jpg" if "jpeg" in tag.mime.lower() else "png"
                os.makedirs(art_dir, exist_ok=True)
                art_path = f"art/{song_id}.{ext}"
                full_path = os.path.join(art_dir, art_path)
                with open(full_path, "wb") as f:
                    f.write(tag.data)
                return art_path
    except Exception as e:
        import logging; logging.getLogger(__name__).debug("Could not extract album art: %s", e)
    return None
