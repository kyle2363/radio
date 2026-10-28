"""Private media plans shared by all outputs; preparation never starts audio."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import inspect
import logging
import math
import os
from types import MappingProxyType
import wave

import config

logger = logging.getLogger(__name__)

class MediaUnavailable(ValueError):
    """The selected entry has no available source media."""


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class MediaItem:
    kind: str
    filepath: str = field(repr=False)  # private server path; never a public descriptor
    song_id: int | None = None
    duration_ms: int = 0
    gain_db: float = 0.0
    role: str | None = None


@dataclass(frozen=True)
class PreparedPlan:
    items: tuple[MediaItem, ...]
    song_id: int | None = None
    session_id: int | None = None
    activation: Mapping = field(default_factory=dict, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "activation", _freeze(self.activation))


def call_announcement(callback, *args, advance):
    """Support existing injected callbacks while production gates can peek."""
    parameters = inspect.signature(callback).parameters
    supports_advance = "advance" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return callback(*args, **({"advance": advance} if supports_advance else {}))


def _duration_ms(filepath, fallback=0):
    try:
        with wave.open(filepath, "rb") as wav:
            return int(wav.getnframes() * 1000 / wav.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        pass
    try:
        from mutagen import File
        media = File(filepath)
        if media is not None and media.info.length > 0:
            return int(media.info.length * 1000)
    except Exception:
        logger.debug("Prepared media duration unavailable", exc_info=True)
    return max(0, int(fallback or 0))


def _announcement_context(queue, pick, song):
    announcements = queue._announcements
    triggers = announcements.AnnounceTrigger
    mood = queue._mood.get_state()
    previous_id = queue._current_song_id
    previous_song = queue._db.get_song(previous_id) if previous_id else None
    context = {
        "current_song": dict(previous_song) if previous_song else {},
        "next_song": dict(song), "mood_state": mood,
        "skip_streak": queue._skip_streak, "hour": datetime.now().hour,
    }
    trigger = triggers.RADIO_INTRO
    if queue._skip_streak >= 1:
        trigger = triggers.SKIP_STREAK
    elif queue._prev_mood_state is not None and queue._prev_mood_state != mood:
        trigger = triggers.MOOD_CHANGE
    elif pick.get("slot") == "wildcard":
        trigger = triggers.WILDCARD
    elif (song.get("recovery_score") or 0) > 0.75:
        trigger = triggers.RECOVERY
    return {"trigger": trigger, **context}, previous_id


def prepare_song(queue, pick):
    song_id = pick.get("song_id")
    song = queue._db.get_song(song_id)
    if not song or not song.get("filepath") or not os.path.isfile(song["filepath"]):
        raise MediaUnavailable("Song source is unavailable")
    filepath = song["filepath"]
    with queue._dj_effect_cache_lock:
        modified = queue._dj_effect_cache.get(song_id)
    if (modified and os.path.isfile(modified)
            and queue._talk_context.get_active_dj_effect_preset()):
        filepath = modified
    try:
        seconds = float(song.get("duration_secs") or 0)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid stored duration")
        duration = int(seconds * 1000) if seconds else _duration_ms(filepath)
    except (TypeError, ValueError, OverflowError):
        duration = _duration_ms(filepath)
    if filepath != song["filepath"]:
        duration = _duration_ms(filepath, duration)
    items = [MediaItem("song", filepath, song_id, duration, song.get("gain_db") or 0.0)]
    activation = {}
    tts_enabled = queue._db.get_setting("tts_enabled") or "true"
    if tts_enabled.lower() == "true" and config.TTS_ENABLED:
        try:
            context, previous_id = _announcement_context(queue, pick, song)
            activation["announcement_context"] = context
            allowed = call_announcement(queue._announcements.should_announce, context, advance=False)
            with queue._pregen_lock:
                cached = dict(queue._pregen_cache.get(song_id) or {})
            path = cached.get("wav_path")
            if (allowed and queue._skip_streak == 0 and path and os.path.isfile(path)
                    and cached.get("prev_song_id") in (None, previous_id)):
                items.insert(0, MediaItem("speech", path, duration_ms=_duration_ms(path), role="announcement"))
                activation["announcement_speech"] = True
        except Exception:
            # The song remains playable if optional announcement metadata fails.
            logger.warning("Announcement preparation unavailable", exc_info=True)
    return PreparedPlan(tuple(items), song_id, queue._current_session_id, activation)


def prepare_talk(queue, pick, fade, concatenate):
    paths = [path for path in pick.get("wav_paths", ()) if path and os.path.isfile(path)]
    if not paths:
        raise MediaUnavailable("Talk source is unavailable")
    faded = fade(paths[0])
    if faded and os.path.isfile(faded):
        paths[0] = faded
    if len(paths) > 1:
        joined = concatenate(paths)
        if joined and os.path.isfile(joined):
            paths = [joined]
    with queue._condition:
        upcoming = next((dict(item) for item in queue._queue if item.get("type") != "talk_segment"), None)
    event = {
        "segment_type": pick.get("segment_type"), "reason": pick.get("reason", ""),
        "persona_id": pick.get("secondary_persona_id") or pick.get("persona_id"),
        "duration_secs": pick.get("duration_secs", 30),
        "next_song_id": upcoming.get("song_id") if upcoming else None,
        "next_song_artist": upcoming.get("artist", "") if upcoming else "",
        "next_song_title": upcoming.get("title", "") if upcoming else "",
    }
    activation = {"talk_segment_playing": bool(upcoming), "talk_event": event,
                  "mood_shift": pick.get("mood_shift")}
    items = tuple(MediaItem("speech", path, duration_ms=_duration_ms(path), role="talk") for path in paths)
    return PreparedPlan(items, session_id=queue._current_session_id, activation=activation)
