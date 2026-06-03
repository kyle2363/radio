"""DJ Effects Engine - real-time audio processing for modified song versions.

Uses pedalboard for high-quality time stretching, pitch shifting, and reverb.
Generated files are cached in data/dj_effect_cache/ and cleaned up on startup.
"""

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime

import config

logger = logging.getLogger(__name__)

EFFECT_CACHE_DIR = os.path.join(config.BASE_DIR, "data", "dj_effect_cache")
_CACHE_LOCK = threading.Lock()


@dataclass
class DJEffectPreset:
    name: str
    display_name: str
    stretch_factor: float
    pitch_shift_semitones: float
    reverb_room_size: float
    reverb_damping: float
    reverb_wet_level: float
    description: str


PRESETS: dict[str, DJEffectPreset] = {
    "slowed_reverb": DJEffectPreset(
        name="slowed_reverb",
        display_name="Slowed + Reverb",
        stretch_factor=0.85,
        pitch_shift_semitones=-3.0,
        reverb_room_size=0.40,
        reverb_damping=0.50,
        reverb_wet_level=0.12,
        description="85% speed, pitch down 3 semitones, subtle reverb",
    ),
    "night_lofi": DJEffectPreset(
        name="night_lofi",
        display_name="Night Lo-Fi",
        stretch_factor=0.90,
        pitch_shift_semitones=-1.0,
        reverb_room_size=0.55,
        reverb_damping=0.60,
        reverb_wet_level=0.20,
        description="90% speed, slight pitch drop, warmer reverb",
    ),
}


def get_available_presets() -> dict:
    """Return all available presets as JSON-safe dicts."""
    return {
        name: {
            "name": p.name,
            "display_name": p.display_name,
            "description": p.description,
        }
        for name, p in PRESETS.items()
    }


def _cache_filename(song_id: int, preset_name: str) -> str:
    """Generate a cache filename for a song+preset combo."""
    return f"{song_id}_{preset_name}.mp3"


def _cache_path(song_id: int, preset_name: str) -> str:
    """Get the full cache path for a song+preset combo."""
    return os.path.join(EFFECT_CACHE_DIR, _cache_filename(song_id, preset_name))


def get_cached(song_id: int, preset_name: str) -> str | None:
    """Return cached filepath if it exists, None otherwise."""
    path = _cache_path(song_id, preset_name)
    if os.path.exists(path):
        return path
    return None


def apply_effect(song_filepath: str, song_id: int, preset_name: str) -> str | None:
    """Apply a DJ effect preset to a song file.

    Processes the file using pedalboard (time stretch → pitch shift → reverb → limiter).
    Writes the result to the cache directory.

    Args:
        song_filepath: Path to the original MP3 file
        song_id: Database song ID (for cache naming)
        preset_name: Name of the preset to use (e.g. "slowed_reverb")

    Returns:
        Path to the processed file, or None on failure
    """
    preset = PRESETS.get(preset_name)
    if not preset:
        logger.warning(f"Unknown DJ effect preset: {preset_name}")
        return None

    output_path = _cache_path(song_id, preset_name)

    try:
        from pedalboard import PitchShift, Reverb, Limiter, time_stretch, Pedalboard
        from pedalboard.io import AudioFile

        with AudioFile(song_filepath) as f:
            audio = f.read(f.frames)
            sr = f.samplerate

        stretched = time_stretch(
            audio,
            sr,
            stretch_factor=preset.stretch_factor,
            high_quality=True,
            transient_mode="crisp",
        )

        board = Pedalboard([
            PitchShift(semitones=preset.pitch_shift_semitones),
            Reverb(
                room_size=preset.reverb_room_size,
                damping=preset.reverb_damping,
                wet_level=preset.reverb_wet_level,
                dry_level=1.00,
                width=1.00,
                freeze_mode=0.00,
            ),
            Limiter(threshold_db=-1.0),
        ])

        effected = board(stretched, sr)

        os.makedirs(EFFECT_CACHE_DIR, exist_ok=True)
        num_channels = effected.shape[0]
        with AudioFile(output_path, "w", sr, num_channels=num_channels) as f:
            f.write(effected)

        logger.info(f"Applied effect '{preset_name}' to song {song_id} → {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"DJ effect failed for song {song_id} ({preset_name}): {e}")
        return None


def get_or_generate(song_filepath: str, song_id: int, preset_name: str) -> str | None:
    """Get cached version or generate a new DJ effect for a song.

    Thread-safe: checks cache under lock, generates if missing.
    """
    with _CACHE_LOCK:
        cached = get_cached(song_id, preset_name)
        if cached:
            return cached

    return apply_effect(song_filepath, song_id, preset_name)


def cleanup_cache(max_age_hours: int = 24):
    """Remove cached effect files older than max_age_hours."""
    if not os.path.isdir(EFFECT_CACHE_DIR):
        os.makedirs(EFFECT_CACHE_DIR, exist_ok=True)
        return

    now = datetime.now()
    removed = 0
    for fname in os.listdir(EFFECT_CACHE_DIR):
        fpath = os.path.join(EFFECT_CACHE_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours > max_age_hours:
                os.remove(fpath)
                removed += 1
        except Exception:
            pass

    if removed > 0:
        logger.info(f"Cleaned {removed} stale DJ effect cache files")


def get_cache_stats() -> dict:
    """Get statistics about the effect cache directory."""
    if not os.path.isdir(EFFECT_CACHE_DIR):
        return {"file_count": 0, "total_bytes": 0, "files": []}

    files = []
    total_bytes = 0
    for fname in sorted(os.listdir(EFFECT_CACHE_DIR)):
        fpath = os.path.join(EFFECT_CACHE_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            size = os.path.getsize(fpath)
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
            files.append({"name": fname, "size": size, "modified": mtime})
            total_bytes += size
        except Exception:
            pass

    return {"file_count": len(files), "total_bytes": total_bytes, "files": files}
