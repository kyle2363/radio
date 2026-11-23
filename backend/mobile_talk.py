"""Connected talk for paired phones (checkpoint 13A).

The phone's connected Radio asks for one talk turn per song gap. This service
decides, with the desktop's own announcer and talk-segment code, whether that
gap gets nothing, a DJ announcement or a talk segment, renders the speech to a
loudness-matched AAC clip and serves it over the mobile gateway.

Each phone context owns a private show (``engine.session_context``), so the
phone never continues or disturbs the desktop's show. Generation is
provisional: the show state a clip would change is only committed when the
phone reports the clip as played or skipped, and is rolled back when it is
missed, discarded, superseded or never reported. The song counter and guest
set length advance only from the song starts the phone reports.

Nothing here touches the desktop queue, playback sessions, ``play_events`` or
``queue_history``.
"""
import copy
import hashlib
import json
import logging
import os
import queue
import random
import secrets
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator

import config
from db import database
from engine.session_context import new_announcement_context, new_talk_context
from mobile_recommendations import MOODS, SONG_ID, Rejected, _existing_ids

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
SEGMENT_TYPES = ("caller", "dj_monologue", "guest_takeover")
MAX_CLIP_SECONDS = 180
CLIP_LIFETIME_SECONDS = 2 * 3600
JOB_LIFETIME_SECONDS = 2 * 3600
TALK_LUFS, TALK_TRUE_PEAK = -10, -1
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_JOBS_PER_SHOW = 8
MAX_SHOWS = 128
TOKEN = r"[A-Za-z0-9_-]{16,64}"
TURN_ID = r"^[A-Za-z0-9_.-]{1,64}$"
GUEST_SHIFT = ("none", "rarely", "normal", "often")
GUEST_SHIFT_CHANCE = {"none": 0.0, "rarely": 0.2, "normal": 0.5, "often": 0.85}
LEAD_IN_MS = (300, 900)  # Silence before a clip, so the DJ doesn't jump in on the song's last note.
_rng = random.Random()


def guest_mood(slot_bias):
    """The mood a guest DJ leans toward, from their persona's slot bias (None when they have no lean)."""
    if not slot_bias:
        return None
    energy, valence = slot_bias.get("energy_range"), slot_bias.get("valence_range")
    if not energy and not valence:
        boosts = slot_bias.get("wildcard_boost", 0) + slot_bias.get("explore_boost", 0)
        return "discovery" if boosts > 0 else None
    from engine.mood import MOOD_STATES
    e = sum(energy) / 2 if energy else 0.5
    v = sum(valence) / 2 if valence else 0.5
    def distance(name):
        state = MOOD_STATES[name]
        return (sum(state["energy_range"]) / 2 - e) ** 2 + (sum(state["valence_range"]) / 2 - v) ** 2
    return min((m for m in MOODS if m not in ("neutral", "discovery") and m in MOOD_STATES), key=distance)
OUTCOMES = ("played", "skipped", "missed", "discarded")

# Talk-segment module state the show owns. Everything else in the namespace
# (prompts, helpers, shared services) stays the desktop's.
TALK_STATE = ("_song_counter", "_current_guest_takeover", "_recent_callers", "_recent_guests",
              "_recent_segment_types", "_recent_monologue_openings", "_recent_monologue_types",
              "_recent_caller_openings", "_recent_guest_styles", "_recent_guest_openings",
              "_last_segment_mood")
ANNOUNCE_STATE = ("_last_announcement_time", "_radio_intro_count", "_songs_since_last_skip")

Title = Annotated[StrictStr, Field(min_length=0, max_length=200)]
Name = Annotated[StrictStr, Field(min_length=1, max_length=80)]


# -- request models --------------------------------------------------------

class SongRef(BaseModel):
    """A catalog song (``song_id``) or an unmatched local file (``title`` from its filename)."""
    model_config = ConfigDict(extra="forbid")
    song_id: Optional[Annotated[StrictStr, Field(pattern=r"^" + SONG_ID + r"$")]] = None
    title: Optional[Title] = None
    source: Annotated[StrictStr, Field(pattern=r"^(catalog|filename)$")] = "catalog"
    slot: Optional[Annotated[StrictStr, Field(pattern=r"^[a-z_]{1,24}$")]] = None

    @model_validator(mode="after")
    def _one_identity(self):
        if self.source == "catalog" and (self.song_id is None or self.title is not None):
            raise ValueError("catalog songs carry song_id only")
        if self.source == "filename" and (self.title is None or self.song_id is not None):
            raise ValueError("filename songs carry title only")
        return self


class Feel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
    name: Name


class FeedbackEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song: SongRef
    liked: StrictBool


class Milestone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
    text: Annotated[StrictStr, Field(min_length=1, max_length=160)]


class SongHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    play_count: Annotated[StrictInt, Field(ge=0, le=1_000_000)]
    days_since: Optional[Annotated[StrictInt, Field(ge=0, le=100_000)]] = None


class LocalTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iso: Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")]
    weekday: Annotated[StrictStr, Field(pattern=r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$")]


class TalkOverrides(BaseModel):
    """Phone overrides; absent fields mean the desktop's current value."""
    model_config = ConfigDict(extra="forbid")
    frequency: Optional[Annotated[StrictInt, Field(ge=2, le=10)]] = None
    callers: Optional[StrictBool] = None
    monologues: Optional[StrictBool] = None
    guests: Optional[StrictBool] = None
    guest_songs: Optional[Annotated[StrictInt, Field(ge=2, le=8)]] = None
    guest_shift: Optional[Annotated[StrictStr, Field(pattern=r"^(" + "|".join(GUEST_SHIFT) + r")$")]] = None


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    turn_id: Annotated[StrictStr, Field(pattern=TURN_ID)]
    turn_seq: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    after_song: SongRef
    next_song: SongRef
    mood: Annotated[StrictStr, Field(pattern=r"^(" + "|".join(MOODS) + r")$")]
    recent_songs: Annotated[list[SongRef], Field(max_length=5)] = []
    feel: Optional[Feel] = None
    feels: Annotated[list[Feel], Field(max_length=24)] = []
    skip_streak: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
    session_feedback: Annotated[list[FeedbackEntry], Field(max_length=10)] = []
    milestones: Annotated[list[Milestone], Field(max_length=8)] = []
    next_song_history: Optional[SongHistory] = None
    local_time: Optional[LocalTime] = None
    session_minutes: Annotated[StrictInt, Field(ge=0, le=100_000)] = 0
    session_songs: Annotated[StrictInt, Field(ge=0, le=100_000)] = 0
    talk: Optional[TalkOverrides] = None


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt
    outcome: Annotated[StrictStr, Field(pattern=r"^(" + "|".join(OUTCOMES) + r")$")]
    heard_s: Optional[Annotated[float | StrictInt, Field(ge=0, le=MAX_CLIP_SECONDS + 60)]] = None


def _parse(model, body):
    try:
        value = model.model_validate(body)
    except ValidationError:
        raise Rejected(422, "Invalid request") from None
    if value.protocol_version != PROTOCOL_VERSION:
        raise Rejected(400, "Unsupported protocol")
    return value


# -- settings --------------------------------------------------------------

def _flag(value, default="true"):
    return "true" if str(value if value is not None else default).strip().lower() == "true" else "false"


def resolve_settings(overrides=None, read=None):
    """Phone override if present, else the desktop's ``settings`` value read fresh."""
    read = read or database.get_setting
    o = overrides or {}

    def number(key, default):
        try:
            return int(str(read(key)).strip())
        except (TypeError, ValueError):
            return default

    def toggle(name, key):
        if name in o:
            return "true" if o[name] else "false"
        return _flag(read(key))

    return {
        "talk_caller_enabled": toggle("callers", "talk_caller_enabled"),
        "talk_monologue_enabled": toggle("monologues", "talk_monologue_enabled"),
        "talk_guest_takeover_enabled": toggle("guests", "talk_guest_takeover_enabled"),
        "talk_frequency": o.get("frequency", number("talk_frequency", 4)),
        "talk_segment_cooldown": number("talk_segment_cooldown", 2),
        "talk_guest_songs_per_set": o.get("guest_songs", number("talk_guest_songs_per_set", 4)),
        "talk_caller_heartbroken_enabled": _flag(read("talk_caller_heartbroken_enabled"), "false"),
        "talk_caller_melancholy_enabled": _flag(read("talk_caller_melancholy_enabled"), "false"),
        "talk_caller_mode": read("talk_caller_mode") or "influences_queue",
        "talk_guest_shift": o.get("guest_shift") or (
            read("talk_guest_shift") if read("talk_guest_shift") in GUEST_SHIFT else "normal"),
    }


# -- audio -----------------------------------------------------------------

def ffmpeg_path():
    return os.environ.get("RADIO_FFMPEG") or shutil.which("ffmpeg")


def render_clip(wav_path, out_path, *, max_seconds=MAX_CLIP_SECONDS):
    """Loudness-normalize to -10 LUFS / -1 dBTP and encode AAC-LC mono 64 kbps in MP4."""
    binary = ffmpeg_path()
    if not binary:
        raise RuntimeError("ffmpeg not found")
    command = [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", wav_path,
               "-t", str(max_seconds),
               "-af", f"loudnorm=I={TALK_LUFS}:TP={TALK_TRUE_PEAK}:LRA=11",
               "-ac", "1", "-ar", "44100", "-c:a", "aac", "-b:a", "64k",
               "-movflags", "+faststart", "-f", "mp4", out_path]
    completed = subprocess.run(command, capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError("ffmpeg failed: " + completed.stderr.decode("utf8", "replace")[-400:])


def _wav_duration_ms(path):
    from engine.playback_plan import _duration_ms
    return _duration_ms(path)


def _join_wavs(paths):
    from announcer import voice_manager
    from announcer.voice_manager import _concatenate_wavs, _fade_in_first_second
    paths = [p for p in paths if p and os.path.isfile(p)]
    os.makedirs(voice_manager.TTS_CACHE_DIR, exist_ok=True)
    if not paths:
        return None
    faded = _fade_in_first_second(paths[0])
    if faded and os.path.isfile(faded):
        paths[0] = faded
    joined = _concatenate_wavs(paths, lead_ms=_rng.randint(*LEAD_IN_MS))
    return joined if joined and os.path.isfile(joined) else None


# -- prompt context --------------------------------------------------------

def _phone_datetime(fixed):
    class PhoneDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    return PhoneDateTime


class _ListenerNotes:
    """Wraps the desktop chat provider to add the phone's listener context to a prompt."""

    def __init__(self, provider_factory, notes):
        self.provider_factory, self.notes = provider_factory, notes

    def __call__(self):
        provider, notes = self.provider_factory(), self.notes

        class Provider:
            def chat(self, *args, messages=None, **kwargs):
                if notes and messages:
                    messages = [dict(m) for m in messages]
                    last = messages[-1]
                    if last.get("role") == "user":
                        last["content"] = (str(last.get("content", "")) + "\n\nListener context (use at most one detail, "
                                           "only if it fits naturally; never read this out as a list):\n"
                                           + "\n".join("- " + line for line in notes))
                return provider.chat(*args, messages=messages, **kwargs)

            def __getattr__(self, name):
                return getattr(provider, name)
        return Provider()


# -- state -----------------------------------------------------------------

@dataclass
class Resource:
    rid: str
    device_id: str
    path: str
    sha256: str
    length: int
    expires_at: float


@dataclass
class Job:
    job_id: str
    device_id: str
    context_id: str
    turn_id: str
    turn_seq: int
    created: float
    state: str = "pending"            # pending | ready | none | failed | cancelled
    metadata: Optional[dict] = None
    after_talk: Optional[dict] = None     # show state to commit on played/skipped
    after_announce: Optional[dict] = None
    outro: bool = False
    outcome: Optional[str] = None
    heard_s: Optional[float] = None
    reply: Optional[dict] = None


@dataclass
class Show:
    device_id: str
    context_id: str
    talk: dict
    announce: dict
    last_seq: int = 0
    last_turn: Optional[str] = None
    last_reply: Optional[dict] = None
    last_mood: Optional[str] = None
    current: Optional[str] = None        # open job id
    jobs: OrderedDict = field(default_factory=OrderedDict)
    touched: float = 0.0


def _snapshot(namespace, names):
    return {name: copy.deepcopy(getattr(namespace, name)) for name in names}


def _load(namespace, state):
    for name, value in state.items():
        setattr(namespace, name, copy.deepcopy(value))


def _initial_state():
    talk, announce = new_talk_context(), new_announcement_context()
    return _snapshot(talk, TALK_STATE), _snapshot(announce, ANNOUNCE_STATE)


class _Worker:
    """One background generation thread: the server serves requests in arrival order."""

    def __init__(self):
        self._queue, self._thread = queue.Queue(), None
        self._lock = threading.Lock()

    def __call__(self, fn):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="mobile-talk", daemon=True)
                self._thread.start()
        self._queue.put(fn)

    def _run(self):
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception:                              # noqa: BLE001 - one bad job must not stop the worker
                logger.exception("Mobile talk job failed")


class MobileTalk:
    def __init__(self, recommendations, *, cache_dir=None, run=None, render=render_clip,
                 clock=time.monotonic, wall=time.time, read_setting=None, available=None, gap_log=None):
        self.recommendations = recommendations
        self.cache_dir = cache_dir or os.path.join(config.DATA_DIR, "mobile_talk")
        self.run = run or _Worker()
        self.render = render
        self.clock, self.wall = clock, wall
        self.read_setting = read_setting or database.get_setting
        self._available = available
        self.gap_log = gap_log or os.path.join(config.DATA_DIR, "logs", "talk_metadata_gaps.jsonl")
        self._gaps = None
        self._lock = threading.RLock()
        self._shows = OrderedDict()        # context_id -> Show
        self._jobs = {}                    # job_id -> Job
        self._resources = {}               # rid -> Resource
        self._generate_lock = threading.Lock()

    # -- capability ---------------------------------------------------------

    def availability(self):
        if self._available is not None:
            return self._available()
        if not config.TTS_ENABLED or _flag(self.read_setting("tts_enabled")) != "true":
            return False, "tts_disabled"
        if not ffmpeg_path():
            return False, "ffmpeg_missing"
        try:
            from announcer.tts import get_tts_status
            status = get_tts_status() or {}
            if isinstance(status, dict) and status.get("available") is False:
                return False, "tts_unavailable"
        except Exception:                                  # noqa: BLE001 - status is advisory
            return False, "tts_unavailable"
        return True, None

    def capability(self):
        available, reason = self.availability()
        return {"available": bool(available), "reason": reason, "segment_types": list(SEGMENT_TYPES),
                "announcements": True, "max_clip_seconds": MAX_CLIP_SECONDS}

    # -- turns --------------------------------------------------------------

    def start_turn(self, device_id, context_id, body):
        with self._lock:
            self.recommendations_owned(device_id, context_id)
        request = _parse(TurnRequest, body)
        refs = [request.after_song, request.next_song, *request.recent_songs,
                *(entry.song for entry in request.session_feedback)]
        ids = {int(ref.song_id) for ref in refs if ref.song_id}
        if ids - _existing_ids(ids):
            raise Rejected(400, "Unknown song IDs")
        available, reason = self.availability()
        with self._lock:
            self.recommendations_owned(device_id, context_id, touch=True)
            self._sweep()
            show = self._show(device_id, context_id)
            if request.turn_id == show.last_turn:
                return dict(show.last_reply)
            if request.turn_seq <= show.last_seq:
                raise Rejected(409, {"reason": "stale_turn"})
            self._cancel_current(show)
            show.last_seq, show.last_turn = request.turn_seq, request.turn_id
            # An actual song start: the counter and guest set advance now, whatever the clip does.
            show.talk["_song_counter"] = show.talk.get("_song_counter", 0) + 1
            takeover = show.talk.get("_current_guest_takeover")
            if takeover:
                takeover["songs_remaining"] -= 1
                takeover.setdefault("played_songs", []).append(self._describe(request.after_song, log=False))
                del takeover["played_songs"][:-8]
            if request.skip_streak > 0:
                show.announce["_songs_since_last_skip"] = 0
            else:
                show.announce["_songs_since_last_skip"] = show.announce.get("_songs_since_last_skip", 0) + 1
            mood_changed = show.last_mood is not None and show.last_mood != request.mood
            show.last_mood = request.mood
            if not available:
                reply = {"state": "none", "reason": reason}
            else:
                job = Job(secrets.token_urlsafe(24), device_id, context_id, request.turn_id, request.turn_seq, self.clock())
                job.outro = bool(takeover and takeover["songs_remaining"] <= 0)
                self._jobs[job.job_id] = job
                show.jobs[job.job_id] = True
                while len(show.jobs) > MAX_JOBS_PER_SHOW:
                    old, _ = show.jobs.popitem(last=False)
                    self._drop_job(old)
                show.current = job.job_id
                reply = {"job_id": job.job_id, "state": "pending"}
                talk_state, announce_state = copy.deepcopy(show.talk), copy.deepcopy(show.announce)
                self.run(lambda: self._generate(job, request, talk_state, announce_state, mood_changed))
            show.last_reply = reply
            return dict(reply)

    def recommendations_owned(self, device_id, context_id, *, touch=False):
        """The owning context. A talk turn is activity on it (``touch``): with Desktop picks off the phone sends only
        talk turns, and the context, with this phone's show, must not idle out between songs."""
        lock = getattr(self.recommendations, "_lock", None)
        if lock is None:
            return self._owned_touch(device_id, context_id, touch)
        with lock:
            return self._owned_touch(device_id, context_id, touch)

    def _owned_touch(self, device_id, context_id, touch):
        context = self.recommendations._owned(device_id, context_id)
        toucher = getattr(self.recommendations, "_touch", None)
        if touch and toucher is not None:
            toucher(context)
        return context

    def _show(self, device_id, context_id):
        show = self._shows.get(context_id)
        if show is None or show.device_id != device_id:
            talk, announce = _initial_state()
            show = Show(device_id, context_id, talk, announce)
            self._shows[context_id] = show
            while len(self._shows) > MAX_SHOWS:
                _, old = self._shows.popitem(last=False)
                for job_id in list(old.jobs):
                    self._drop_job(job_id)
        self._shows.move_to_end(context_id)
        show.touched = self.clock()
        return show

    def _cancel_current(self, show):
        job = self._jobs.get(show.current) if show.current else None
        show.current = None
        if job is None or job.outcome is not None:
            return
        if job.state in ("pending", "ready"):
            job.state = "cancelled"
            self._release(job)
        self._end_unplayed_outro(show, job)

    def _end_unplayed_outro(self, show, job):
        """A missed outro still ends the guest set."""
        takeover = show.talk.get("_current_guest_takeover")
        if job.outro and takeover and takeover["songs_remaining"] <= 0:
            show.talk["_current_guest_takeover"] = None
            recent = show.talk.setdefault("_recent_guests", [])
            recent.append(takeover["persona_id"])
            del recent[:-10]

    # -- generation ---------------------------------------------------------

    def _generate(self, job, request, talk_state, announce_state, mood_changed):
        with self._lock:
            if job.state != "pending":
                return
        try:
            with self._generate_lock:
                result = self._decide_and_render(job, request, talk_state, announce_state, mood_changed)
        except Exception:                                  # noqa: BLE001
            logger.exception("Mobile talk generation failed")
            result = None
        with self._lock:
            show = self._shows.get(job.context_id)
            if job.state != "pending":
                if result and result.get("resource"):
                    self._remove_file(result["resource"].path)
                return
            if not result or result.get("none"):
                job.state = "none" if result is not None else "failed"
                # No clip: whatever the decision changed is committed now.
                if show is not None and show.current == job.job_id and result is not None:
                    show.talk, show.announce = result["talk"], result["announce"]
                if show is not None and show.current == job.job_id and job.outro:
                    self._end_unplayed_outro(show, job)
                return
            resource = result["resource"]
            self._resources[resource.rid] = resource
            job.state, job.metadata = "ready", result["metadata"]
            job.after_talk, job.after_announce = result["talk"], result["announce"]

    def _decide_and_render(self, job, request, talk_state, announce_state, mood_changed):
        settings = resolve_settings(request.talk.model_dump(exclude_none=True) if request.talk else {},
                                    self.read_setting)
        talk, announce = new_talk_context(), new_announcement_context()
        _load(talk, talk_state)
        _load(announce, announce_state)
        when = self._phone_time(request.local_time)
        talk.datetime = announce.datetime = _phone_datetime(when)
        after = self._describe(request.after_song)
        nxt = self._describe(request.next_song)
        recent = [self._describe(ref) for ref in request.recent_songs] or [after]
        notes = self._notes(request, nxt)
        talk.llm_chat = _ListenerNotes(talk.llm_chat, notes)
        announce.llm_chat = _ListenerNotes(announce.llm_chat, notes)
        artists = [s["artist"] for s in recent if s.get("artist") and s["artist"] != "unknown artist"]
        talk._get_session_context = lambda: {"session_minutes": request.session_minutes,
                                             "song_count": request.session_songs,
                                             "top_artists": list(dict.fromkeys(artists))[:3]}
        mood = request.mood
        segment, kind = None, "talk"
        takeover = talk._current_guest_takeover
        if takeover and takeover["songs_remaining"] <= 0:
            segment = talk.generate_guest_takeover_outro(takeover["persona_id"], played_songs=takeover.get("played_songs", []),
                                                         set_mood=mood)
            talk._current_guest_takeover = None
            talk._recent_guests.append(takeover["persona_id"])
            del talk._recent_guests[:-10]
        elif not takeover:
            segment = talk.get_next_talk_segment(
                settings=settings, mood_state=mood, caller_mode=settings["talk_caller_mode"],
                next_song=nxt, recent_context=recent, last_song=after,
                session_state={"skip_streak": request.skip_streak, "mood_changed_recently": mood_changed,
                               "hour": when.hour, "session_duration_minutes": request.session_minutes},
                increment=False)
        if job.state != "pending":
            return None
        text = None
        if segment is None:
            text, trigger = self._announcement(announce, request, after, nxt, when, mood, mood_changed)
            kind = "announcement"
        after_talk, after_announce = _snapshot(talk, TALK_STATE), _snapshot(announce, ANNOUNCE_STATE)
        if segment is None and not text:
            return {"none": True, "talk": after_talk, "announce": after_announce}
        if job.state != "pending":
            return None
        if segment is not None:
            wavs, metadata = segment.wav_paths, self._talk_metadata(segment, settings)
        else:
            wav = announce.play_announcement(text, "", trigger_type=trigger.value,
                                             context={"mood_state": mood, "next_song": nxt, "hour": when.hour})
            if not wav:
                return None
            wavs, metadata = [wav], self._announcement_metadata(text, trigger.value)
            after_announce["_last_announcement_time"] = "played"
        joined = _join_wavs(wavs)
        if not joined:
            return None
        resource = self._encode(job, joined)
        metadata["duration_ms"] = min(_wav_duration_ms(joined) or 0, MAX_CLIP_SECONDS * 1000)
        metadata["resource"] = {"id": resource.rid, "sha256": resource.sha256, "byte_length": resource.length,
                                "mime": "audio/mp4", "expires_at": int(resource.expires_at)}
        metadata["kind"] = kind
        return {"metadata": metadata, "resource": resource, "talk": after_talk, "announce": after_announce}

    def _announcement(self, announce, request, after, nxt, when, mood, mood_changed):
        """The desktop's trigger priority: skip streak > mood change > wildcard > recovery > radio intro."""
        Trigger = announce.AnnounceTrigger
        trigger = Trigger.RADIO_INTRO
        if request.skip_streak >= 1:
            trigger = Trigger.SKIP_STREAK
        elif mood_changed:
            trigger = Trigger.MOOD_CHANGE
        elif request.next_song.slot == "wildcard":
            trigger = Trigger.WILDCARD
        elif nxt.get("recovery_score") is not None and nxt["recovery_score"] > 0.75:
            trigger = Trigger.RECOVERY
        context = {"current_song": after, "next_song": nxt, "mood_state": mood,
                   "skip_streak": request.skip_streak, "hour": when.hour}
        # The skip tracker already advanced with the reported song start.
        songs_since_skip = announce._songs_since_last_skip
        allowed = announce.should_announce({"trigger": trigger, **context}, advance=True)
        announce._songs_since_last_skip = songs_since_skip
        if not allowed:
            return None, trigger
        return announce.get_announcement(context, trigger, advance=False), trigger

    def _phone_time(self, local_time):
        if local_time is not None:
            try:
                return datetime.fromisoformat(local_time.iso)
            except ValueError:
                pass
        return datetime.now()

    def _notes(self, request, nxt):
        notes = []
        if request.local_time is not None:
            notes.append(f"It is {request.local_time.weekday.title()} for the listener.")
        if request.feel is not None:
            notes.append(f"The listener is playing the '{request.feel.name}' feel station.")
        history = request.next_song_history
        if history is not None and nxt.get("title"):
            favourite = self._int_setting("talk_favourite_play_count", 10)
            ages = self._int_setting("talk_long_absence_days", 60)
            if history.play_count >= favourite:
                notes.append(f"'{nxt['title']}' is one of the listener's favourites.")
            elif history.days_since is not None and history.days_since >= ages:
                notes.append(f"The listener hasn't heard '{nxt['title']}' in ages.")
        for entry in request.session_feedback[:3]:
            song = self._describe(entry.song, log=False)
            notes.append(f"The listener {'liked' if entry.liked else 'disliked'} '{song['title']}' this session.")
        for milestone in request.milestones[:2]:
            notes.append(f"Listener milestone: {milestone.text}")
        return notes

    def _int_setting(self, key, default):
        try:
            return int(str(self.read_setting(key)).strip())
        except (TypeError, ValueError):
            return default

    def _describe(self, ref, log=True):
        """Prompt-ready artist/title for a catalog or filename song; logs metadata gaps."""
        if ref.song_id:
            song = database.get_song(int(ref.song_id)) or {}
            artist, title = (song.get("artist") or "").strip(), (song.get("title") or "").strip()
            lyrics = song.get("lyrics") or ""
            if log:
                for name, value in (("artist", artist), ("title", title)):
                    if not value:
                        self._log_gap("catalog:" + ref.song_id, name)
            return {"song_id": int(ref.song_id), "artist": artist or "Unknown", "title": title or "Unknown",
                    "lyrics_snippet": " ".join(lyrics.split()[:getattr(config, "LYRICS_TALK_WORDS", 40)]),
                    "recovery_score": song.get("recovery_score")}
        title = (ref.title or "").strip()
        if log and not title:
            self._log_gap("filename:" + (ref.title or ""), "title")
        return {"artist": "unknown artist",
                "title": (title + " (rough title from a filename)") if title else "an untitled track",
                "lyrics_snippet": ""}

    def _log_gap(self, key, missing):
        try:
            if self._gaps is None:
                self._gaps = set()
                if os.path.isfile(self.gap_log):
                    with open(self.gap_log, encoding="utf-8") as handle:
                        for line in handle:
                            try:
                                entry = json.loads(line)
                                self._gaps.add((entry["song"], entry["missing"]))
                            except (ValueError, KeyError, TypeError):
                                continue
            if (key, missing) in self._gaps:
                return
            self._gaps.add((key, missing))
            os.makedirs(os.path.dirname(self.gap_log), exist_ok=True)
            with open(self.gap_log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"song": key, "missing": missing,
                                         "first_seen": datetime.now().isoformat(timespec="seconds")}) + "\n")
        except OSError:
            logger.debug("Could not record talk metadata gap", exc_info=True)

    def _talk_metadata(self, segment, settings):
        from announcer.personas import get_persona
        kind = segment.type.value
        dj = get_persona(segment.persona_id or "dj")
        dj_name = dj.name if dj else "DJ"
        other_id = segment.secondary_persona_id
        other = get_persona(other_id) if other_id else None
        other_name = other.name if other else "Guest"
        speakers = [{"id": "dj", "name": dj_name}]
        parts = [p.strip() for p in (segment.text or "").split(" | ")]
        if kind == "caller":
            speakers.append({"id": other_id, "name": other_name})
            transcript = []
            for index, line in enumerate(parts):
                if not line:
                    continue
                speaker = "dj" if index == len(parts) - 1 or index % 2 == 0 else other_id
                transcript.append({"speaker": speaker, "text": line})
            title = f"{other_name} on the line"
        elif kind.startswith("guest_takeover"):
            speakers = [{"id": other_id, "name": other_name}]
            transcript = [{"speaker": other_id, "text": segment.text or ""}]
            title = f"{other_name} takes over" if kind.endswith("intro") else f"{other_name} signs off"
        else:
            transcript = [{"speaker": "dj", "text": segment.text or ""}]
            title = f"{dj_name} on the mic"
        shift = None
        if segment.mood_shift in MOODS and settings.get("talk_caller_mode") != "narrative_only":
            shift = {"mood": segment.mood_shift, "feel": None, "restore_at_end": False}
        guest_set = None
        if kind == "guest_takeover_intro" and settings.get("talk_caller_mode") != "narrative_only":
            # A guest may bring their own sound; the phone reverts it when the set ends.
            chance = GUEST_SHIFT_CHANCE.get(settings.get("talk_guest_shift", "normal"), 0.5)
            mood = guest_mood(other.slot_bias if other else None)
            if mood and _rng.random() < chance:
                shift = {"mood": mood, "feel": None, "restore_at_end": True}
        if kind == "guest_takeover_intro":
            guest_set = {"persona": other_id, "name": other_name, "songs": settings["talk_guest_songs_per_set"]}
        elif kind == "guest_takeover_outro":
            guest_set = {"ends": True}
        return {"segment_type": kind, "title": title, "speakers": speakers,
                "transcript": [t for t in transcript if t["text"]], "shift": shift, "guest_set": guest_set}

    def _announcement_metadata(self, text, trigger):
        from announcer.personas import get_persona
        dj = get_persona("dj")
        name = dj.name if dj else "DJ"
        return {"segment_type": "announcement", "trigger": trigger, "title": f"{name} on the mic",
                "speakers": [{"id": "dj", "name": name}], "transcript": [{"speaker": "dj", "text": text}],
                "shift": None, "guest_set": None}

    def _encode(self, job, wav):
        os.makedirs(self.cache_dir, exist_ok=True)
        rid = secrets.token_urlsafe(24)
        path = os.path.join(self.cache_dir, rid + ".m4a")
        try:
            self.render(wav, path)
            size = os.path.getsize(path)
            if not 0 < size <= MAX_RESOURCE_BYTES:
                raise RuntimeError("Talk clip size out of range")
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
        except BaseException:
            self._remove_file(path)
            raise
        return Resource(rid, job.device_id, path, digest, size, self.wall() + CLIP_LIFETIME_SECONDS)

    # -- jobs and resources -------------------------------------------------

    def job(self, device_id, job_id):
        with self._lock:
            self._sweep()
            job = self._owned_job(device_id, job_id)
            reply = {"job_id": job.job_id, "turn_id": job.turn_id, "state": job.state}
            if job.state == "ready":
                reply.update(job.metadata)
            if job.outcome:
                reply["outcome"] = job.outcome
            return reply

    def resource(self, device_id, rid):
        with self._lock:
            self._sweep()
            resource = self._resources.get(rid)
            if resource is None or resource.device_id != device_id:
                raise Rejected(404, "talk_resource_not_found")
            path = resource.path
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            raise Rejected(404, "talk_resource_not_found") from None

    def outcome(self, device_id, job_id, body):
        with self._lock:
            self._owned_job(device_id, job_id)
        request = _parse(OutcomeRequest, body)
        with self._lock:
            job = self._owned_job(device_id, job_id)
            if job.outcome is not None:
                if job.outcome == request.outcome:
                    return {"job_id": job_id, "outcome": job.outcome, "committed": job.outcome in ("played", "skipped")}
                raise Rejected(409, {"reason": "outcome_recorded"})
            if request.outcome in ("played", "skipped") and job.state != "ready":
                raise Rejected(409, {"reason": "not_ready"})
            job.outcome = request.outcome
            job.heard_s = float(request.heard_s) if request.heard_s is not None else None
            show = self._shows.get(job.context_id)
            committed = False
            if show is not None and show.current == job.job_id:
                if request.outcome in ("played", "skipped") and job.after_talk is not None:
                    show.talk = job.after_talk
                    announce = dict(job.after_announce)
                    if announce.get("_last_announcement_time") == "played":
                        announce["_last_announcement_time"] = self.wall()
                    show.announce = announce
                    committed = True
                else:
                    self._end_unplayed_outro(show, job)
                show.current = None
            if job.state in ("pending", "ready") and request.outcome in ("missed", "discarded"):
                job.state = "cancelled"
            if request.outcome in ("missed", "discarded"):
                self._release(job)
            return {"job_id": job_id, "outcome": job.outcome, "committed": committed}

    def _owned_job(self, device_id, job_id):
        job = self._jobs.get(job_id)
        if job is None or job.device_id != device_id:
            raise Rejected(404, "talk_job_not_found")
        return job

    def _release(self, job):
        if job.metadata and job.metadata.get("resource"):
            resource = self._resources.pop(job.metadata["resource"]["id"], None)
            if resource:
                self._remove_file(resource.path)

    def _drop_job(self, job_id):
        job = self._jobs.pop(job_id, None)
        if job is not None:
            self._release(job)

    @staticmethod
    def _remove_file(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _sweep(self):
        now, wall = self.clock(), self.wall()
        for rid in [r for r, res in self._resources.items() if res.expires_at <= wall]:
            self._remove_file(self._resources.pop(rid).path)
        for job_id in [j for j, job in self._jobs.items() if now - job.created >= JOB_LIFETIME_SECONDS]:
            job = self._jobs[job_id]
            show = self._shows.get(job.context_id)
            if show is not None and show.current == job_id:
                if job.outcome is None:
                    self._end_unplayed_outro(show, job)
                show.current = None
            if show is not None:
                show.jobs.pop(job_id, None)
            self._drop_job(job_id)
        contexts = getattr(self.recommendations, "_contexts", None)
        if contexts is not None:
            for context_id in [c for c in self._shows if c not in contexts]:
                show = self._shows.pop(context_id)
                for job_id in list(show.jobs):
                    job = self._jobs.get(job_id)
                    if job is not None and job.state == "pending":
                        job.state = "cancelled"
                    self._drop_job(job_id)

    def forget_device(self, device_id):
        with self._lock:
            for context_id in [c for c, s in self._shows.items() if s.device_id == device_id]:
                del self._shows[context_id]
            for job_id in [j for j, job in self._jobs.items() if job.device_id == device_id]:
                job = self._jobs[job_id]
                if job.state == "pending":
                    job.state = "cancelled"
                self._drop_job(job_id)
            for rid in [r for r, res in self._resources.items() if res.device_id == device_id]:
                self._remove_file(self._resources.pop(rid).path)


__all__ = ["MobileTalk", "resolve_settings", "render_clip", "TOKEN"]
