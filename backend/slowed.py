"""Slowed Radio on the desktop (checkpoint 14B, docs/SLOWED_RADIO_PLAN.md).

The phone slows its own files down; the desktop only writes and voices what the effects guest says about it:

* **Bank lines** - generic opener / mid-set / closer lines (no songs, times or dates), generated ahead of time in
  the background while the desktop is idle, stored in ``data/slowed_lines/`` + the ``slowed_lines`` table, and
  synced to the phone through a revisioned manifest so Slowed sets also work offline.
* **Live lines** - written for one talk turn (they may name the last or next song) when the phone asks with an
  ``effects`` request; see ``mobile_talk``.

Generation is strictly sequential (ingest rule: no parallel LLM calls) and the bank worker runs only while nothing
else is speaking or ingesting.
"""
import hashlib
import json
import logging
import os
import random
import re
import secrets
import threading
import time
from datetime import date

import config
from db import database

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
# Keep in step with SlowedPreset.kt and the ``slowed_presets`` seed row.
PRESETS = {
    "really_slow": {"label": "Really slow", "rate": 0.75, "room": 0.60, "damping": 0.45, "wet": 0.22, "weight": 20,
                    "description": "way down, deep and heavy, with lots of space and echo"},
    "slowed": {"label": "Slowed", "rate": 0.85, "room": 0.40, "damping": 0.50, "wet": 0.12, "weight": 50,
               "description": "a little slower and lower, with a soft echo"},
    "lofi": {"label": "Lo-fi", "rate": 0.90, "room": 0.55, "damping": 0.60, "wet": 0.20, "weight": 30,
             "description": "just a touch slower, warm and hazy"},
}
GUESTS = ("guest_chill", "guest_undertow")
MOMENTS = ("opener", "mid", "closer")
TRIGGERS = ("auto", "manual")
LINE_ID = r"[A-Za-z0-9_-]{16,64}"

# Bank kinds: per-preset counts (preset set) or shared (preset None). Plan section 5.
KINDS = {
    "opener_auto": {"per_preset": 12},
    "opener_manual": {"per_preset": 8},
    "mid": {"shared": 10},
    "closer": {"per_preset": 10},
    "closer_generic": {"shared": 12},
}
MAX_BATCHES_PER_GAP = 3
MAX_BATCH = 12
RETIRE_FRACTION = 0.2

MOMENT_TEXT = {
    "opener_auto": "You're taking over for a few songs and slowing every one of them down.",
    "opener_manual": ("The listener just asked for it: the song is fading out and you're slowing the station down for a "
                      "while. Acknowledge that they asked."),
    "mid": "You're partway through your slowed set; remind the listener it's still slowed. Keep it very short.",
    "closer": "Your slowed set is over; songs go back to normal speed and you hand back to the station.",
    "closer_generic": ("Your slowed set is over; songs go back to normal speed and you hand back to the station. "
                       "Do not describe the effect."),
}
WORDS = {"opener_auto": (18, 35), "opener_manual": (15, 30), "mid": (6, 15), "closer": (12, 28), "closer_generic": (10, 22)}
SLOW_CUE = "Make it unmistakable that the music is about to slow down"
CUE_RULE = {"opener_auto": SLOW_CUE, "opener_manual": SLOW_CUE, "mid": "Say the music is still slowed",
            "closer": "Make it clear the music is back to normal speed now",
            "closer_generic": "Make it clear the music is back to normal speed now"}
# A line must carry its cue: a slowed word for openers/mid, a back-to-normal phrase for closers.
SLOW_WORDS = re.compile(r"\b(slow|slows|slowed|slowing|slower|down|under|sink|sinking|drift|drifting|deeper|heavy|"
                        r"lower|easing|ease|unhurried|crawl|molasses|tempo)\b", re.I)
NORMAL_WORDS = re.compile(r"\b(normal|back up|full speed|regular|usual|speed back|up to speed|surface|surfacing|"
                          r"bring it back|back to the station|back to you|handing back|hand it back|original)\b", re.I)
TIME_WORDS = re.compile(
    r"\b(morning|afternoon|evening|tonight|today|tomorrow|yesterday|midnight|noon|dawn|dusk|sunset|sunrise|weekend|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|"
    r"september|october|november|december|spring|summer|autumn|fall|winter|christmas|o'clock|am|pm|rain|raining|"
    r"snow|snowing|sunny|weather|"
    r"phone|app|apps|recording|recorded|settings|ai|algorithm)\b", re.I)
EXTRA_NOISE = re.compile(r"[\"*_#\[\]{}<>]|\(.*?\)")

BANK_PROMPT = """You are {guest_name}, a guest DJ on a personal radio station.
Your personality: {guest_personality}

Write {count} different short spoken lines for this moment of your slowed set:
{moment}

The effect: {preset_description}

Rules:
- Each line is {min_words}-{max_words} words
- {cue_rule}
- Never name a song, artist, album, time of day, day, date, weather, season or place; these lines are replayed at any time
- Never mention phones, apps, recordings, settings or being an AI
- Every line opens differently; vary rhythm and imagery
- Do not reuse these openings: {existing_openings}
- Plain spoken words only: no quotes, labels, emojis, stage directions or sound effects
Return JSON only: {{"lines": ["...", "..."]}}"""

LIVE_OPENER_PROMPT = """You are {guest_name}, a guest DJ taking over for the next {songs} songs, and you are slowing every one of them down.
Your personality: {guest_personality}

Context:
- Time of day: {greeting}
- Last song that just played: {last_song}
- First song of your slowed set: {next_song}
- The effect: {preset_description}
- Why now: {trigger}

Write an 8-14 second spoken intro (20-35 words).

Rules:
- Make it unmistakable that the music is about to slow down
- You may name the last song or the next song, at most one of them
- Avoid repeating recent openings: {recent_openings}
- Invent no facts about songs or artists
- Sound like yourself, slow and unhurried
- No quotes, no labels, no stage directions; just the spoken words"""

LIVE_MID_PROMPT = """You are {guest_name}, partway through your slowed set.
Your personality: {guest_personality}

Context:
- Song that just played (slowed): {last_song}
- Songs left in your set: {songs_left}
- The effect: {preset_description}

Write a 3-5 second line (8-15 words) reminding the listener the music is still slowed.
- You may mention the song that just played
- No quotes, no labels, no stage directions; just the spoken words"""

LIVE_CLOSER_PROMPT = """You are {guest_name}, ending your slowed set and handing the show back.
Your personality: {guest_personality}

Context:
- Songs you just played slowed: {played_songs}
- Main DJ: {dj_name}
- The effect: {preset_description}

Write a 6-12 second spoken handoff (15-30 words).
- Make it clear the music is going back to normal speed now
- You may mention one track from your set
- Feel like a natural ending, not a formal sign-off
- No quotes, no labels, no stage directions; just the spoken words"""

LIVE_WORDS = {"opener": (15, 40), "mid": (5, 18), "closer": (12, 34)}
TRIGGER_TEXT = {"manual": "the listener asked for it", "auto": "you felt the station needed it"}


# -- line checks -------------------------------------------------------------

def clean(text):
    text = (text or "").strip().strip("\"'").strip()
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split())


def opening(text, words=4):
    return " ".join(re.findall(r"[a-z']+", text.lower())[:words])


def _similar(a, b):
    """Jaccard similarity of word sets; generic lines that share most words are repeats."""
    x, y = set(re.findall(r"[a-z']+", a.lower())), set(re.findall(r"[a-z']+", b.lower()))
    return len(x & y) / max(1, len(x | y))


def check_line(text, kind, *, existing=(), names=(), allow_names=False, words=None):
    """Why [text] is rejected for [kind], or None when it passes (plan section 5.1 step 2)."""
    if not text:
        return "empty"
    low, high = words or WORDS[kind]
    count = len(text.split())
    if not low <= count <= high:
        return "length"
    if EXTRA_NOISE.search(text):
        return "markup"
    closer = kind.startswith("closer")
    if closer and not NORMAL_WORDS.search(text):
        return "cue"
    if not closer and not SLOW_WORDS.search(text):
        return "cue"
    if TIME_WORDS.search(text):
        return "time"
    if not allow_names:
        lowered = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
        for name in names:
            if name and len(name) >= 4 and (" " + name + " ") in lowered:
                return "name"
    head = opening(text)
    for other in existing:
        if opening(other) == head or _similar(other, text) >= 0.6:
            return "repeat"
    return None


def library_names(limit=4000):
    """Lower-cased artist and title strings from the library, for the no-names rule."""
    names = set()
    try:
        with database.get_connection_cm() as conn:
            for artist, title in conn.execute("SELECT artist, title FROM songs LIMIT ?", (limit,)):
                for value in (artist, title):
                    value = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
                    value = " ".join(value.split())
                    if len(value) >= 4 and value not in ("unknown", "untitled"):
                        names.add(value)
    except Exception:                                      # noqa: BLE001 - names are a filter, not a requirement
        logger.debug("Could not read library names", exc_info=True)
    return names


# -- generation helpers -------------------------------------------------------

def _chat(prompt, temperature=0.8):
    from llm import llm_chat
    response = llm_chat().chat(model=None, messages=[{"role": "user", "content": prompt}],
                               options={**config.OLLAMA_CHAT_OPTIONS, "temperature": temperature}, think=False)
    return response.content or ""


def _parse_lines(raw):
    raw = (raw or "").strip()
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(match.group(0) if match else raw)
        lines = data.get("lines") if isinstance(data, dict) else None
    except (ValueError, AttributeError):
        lines = None
    return [clean(line) for line in lines if isinstance(line, str)] if isinstance(lines, list) else []


def voice(text, guest_id, *, mood_state="chill"):
    """Speak [text] with the guest's own voice and speed (same path as guest intros). Returns a WAV path or None."""
    from announcer import speech_direction
    from announcer.personas import get_persona
    from announcer.tts import tts_say
    guest = get_persona(guest_id)
    if guest is None:
        return None
    spoken = speech_direction.prepare_line(text, rng=random, mark_probability=0.2)
    direction = speech_direction.direction_for_line(None, mood_state=mood_state, persona_id=guest.id, context={})
    return tts_say(spoken, voice_id=guest.voice_id, speed=guest.speed_base, direction=direction)


def live_line(moment, guest_id, preset_id, *, songs=4, trigger="auto", last_song=None, next_song=None,
              songs_left=0, played_songs=(), hour=None, recent_openings=(), chat=None, names=()):
    """A connected-turn line (plan 13.4-13.6): may name the last or next song. One retry; None when both fail."""
    from announcer.personas import get_persona
    guest, dj = get_persona(guest_id), get_persona("dj")
    preset = PRESETS.get(preset_id)
    if guest is None or preset is None or moment not in MOMENTS:
        return None
    chat = chat or _chat

    def song(s):
        return f"{s.get('artist', 'Unknown')} - {s.get('title', 'Unknown')}" if s else "(not known)"
    if moment == "opener":
        from announcer.talk_segments import _build_greeting
        prompt = LIVE_OPENER_PROMPT.format(
            guest_name=guest.name, guest_personality=guest.personality, songs=songs,
            greeting=_build_greeting(hour if hour is not None else 21), last_song=song(last_song),
            next_song=song(next_song), preset_description=preset["description"],
            trigger=TRIGGER_TEXT.get(trigger, TRIGGER_TEXT["auto"]),
            recent_openings="; ".join(recent_openings) or "none yet")
    elif moment == "mid":
        prompt = LIVE_MID_PROMPT.format(guest_name=guest.name, guest_personality=guest.personality,
                                        last_song=song(last_song), songs_left=songs_left,
                                        preset_description=preset["description"])
    else:
        prompt = LIVE_CLOSER_PROMPT.format(guest_name=guest.name, guest_personality=guest.personality,
                                           played_songs="; ".join(song(s) for s in played_songs) or "a few slowed tracks",
                                           dj_name=dj.name if dj else "the main DJ",
                                           preset_description=preset["description"])
    kind = {"opener": "opener_auto", "mid": "mid", "closer": "closer"}[moment]
    for _ in range(2):
        try:
            text = clean(chat(prompt))
        except Exception:                                  # noqa: BLE001 - a failed live line falls back to the bank
            logger.exception("Slowed live line failed")
            return None
        # Live lines may name songs and the time of day (the opener is given it); everything else is checked.
        reason = check_line(text, kind, existing=recent_openings, names=names, allow_names=True, words=LIVE_WORDS[moment])
        if reason in (None, "time"):
            return text
        logger.info("Slowed live %s rejected (%s): %s", moment, reason, text[:120])
    return None


# -- the bank -----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS slowed_lines (
    id TEXT PRIMARY KEY,
    guest TEXT NOT NULL,
    kind TEXT NOT NULL,
    preset TEXT,
    text TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    created REAL NOT NULL,
    added_rev INTEGER NOT NULL,
    retired REAL,
    retired_rev INTEGER,
    plays INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_slowed_lines_rev ON slowed_lines(added_rev);
CREATE TABLE IF NOT EXISTS slowed_retirements (guest TEXT PRIMARY KEY, day TEXT NOT NULL);
"""


def targets(guests=GUESTS, presets=tuple(PRESETS)):
    """(guest, kind, preset) -> how many live lines the bank keeps."""
    result = {}
    for guest in guests:
        for kind, spec in KINDS.items():
            if "per_preset" in spec:
                for preset in presets:
                    result[(guest, kind, preset)] = spec["per_preset"]
            else:
                result[(guest, kind, None)] = spec["shared"]
    return result


class SlowedBank:
    def __init__(self, *, directory=None, connect=None, render=None, duration=None, wall=time.time, today=date.today):
        self.directory = directory or os.path.join(config.DATA_DIR, "slowed_lines")
        self.connect = connect or database.get_connection_cm
        self._render = render
        self._duration = duration
        self.wall, self.today = wall, today
        self._lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # -- reads ------------------------------------------------------------------

    def revision(self, conn=None):
        def read(c):
            row = c.execute("SELECT MAX(MAX(added_rev), COALESCE(MAX(retired_rev), 0)) FROM slowed_lines").fetchone()
            return int(row[0] or 0)
        if conn is not None:
            return read(conn)
        with self.connect() as c:
            return read(c)

    def manifest(self, since=0):
        """Lines added and ids retired after revision [since]; metadata only."""
        with self._lock, self.connect() as conn:
            revision = self.revision(conn)
            since = since if 0 <= since <= revision else 0
            added = conn.execute(
                "SELECT id, guest, kind, preset, sha256, bytes, duration_ms, created FROM slowed_lines "
                "WHERE retired IS NULL AND added_rev > ? ORDER BY added_rev, id", (since,)).fetchall()
            retired = [row[0] for row in conn.execute(
                "SELECT id FROM slowed_lines WHERE retired IS NOT NULL AND retired_rev > ? AND added_rev <= ? ORDER BY id",
                (since, since))]
        return {"protocol_version": PROTOCOL_VERSION, "revision": revision, "full": since == 0,
                "lines": [{"id": r[0], "guest": r[1], "kind": r[2], "preset": r[3], "sha256": r[4], "byte_length": r[5],
                           "duration_ms": r[6], "mime": "audio/mp4", "created": int(r[7])} for r in added],
                "retired": retired}

    def read(self, line_id):
        if not re.fullmatch(LINE_ID, line_id or ""):
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT sha256 FROM slowed_lines WHERE id = ? AND retired IS NULL", (line_id,)).fetchone()
        if row is None:
            return None
        try:
            with open(os.path.join(self.directory, line_id + ".m4a"), "rb") as handle:
                data = handle.read()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == row[0] else None

    def texts(self, guest, kind, preset=None, conn=None):
        def read(c):
            return [r[0] for r in c.execute(
                "SELECT text FROM slowed_lines WHERE retired IS NULL AND guest = ? AND kind = ? AND preset IS ?",
                (guest, kind, preset))]
        if conn is not None:
            return read(conn)
        with self.connect() as c:
            return read(c)

    def gaps(self, wanted=None):
        """[(guest, kind, preset, missing)] in a stable order, openers and closers first."""
        wanted = wanted or targets()
        with self.connect() as conn:
            have = {(g, k, p): n for g, k, p, n in conn.execute(
                "SELECT guest, kind, preset, COUNT(*) FROM slowed_lines WHERE retired IS NULL GROUP BY guest, kind, preset")}
        order = {"opener_auto": 0, "closer_generic": 1, "closer": 2, "opener_manual": 3, "mid": 4}
        missing = [(g, k, p, n - have.get((g, k, p), 0)) for (g, k, p), n in wanted.items() if have.get((g, k, p), 0) < n]
        return sorted(missing, key=lambda m: (order[m[1]], m[0], m[2] or ""))

    def stats(self):
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM slowed_lines WHERE retired IS NULL").fetchone()
        return {"lines": row[0], "bytes": row[1], "revision": self.revision(), "missing": sum(m[3] for m in self.gaps())}

    # -- writes -----------------------------------------------------------------

    def add(self, guest, kind, preset, text, wav):
        """Encode [wav] as a bank line. Returns the new id."""
        if guest not in GUESTS or kind not in KINDS or (preset is not None and preset not in PRESETS):
            raise ValueError("Unknown Slowed line")
        if ("per_preset" in KINDS[kind]) != (preset is not None):
            raise ValueError("Preset does not match the line kind")
        os.makedirs(self.directory, exist_ok=True)
        line_id = secrets.token_urlsafe(18)
        path = os.path.join(self.directory, line_id + ".m4a")
        render = self._render
        if render is None:
            from mobile_talk import render_clip as render
        try:
            render(wav, path)
            with open(path, "rb") as handle:
                data = handle.read()
            from mobile_talk import MAX_RESOURCE_BYTES
            if not 0 < len(data) <= MAX_RESOURCE_BYTES:
                raise RuntimeError("Slowed line size out of range")
            duration = self._duration(wav) if self._duration else _wav_duration_ms(wav)
            with self._lock, self.connect() as conn:
                revision = self.revision(conn) + 1
                conn.execute("INSERT INTO slowed_lines (id, guest, kind, preset, text, sha256, bytes, duration_ms, created, added_rev) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (line_id, guest, kind, preset, text, hashlib.sha256(data).hexdigest(), len(data),
                              int(duration or 0), self.wall(), revision))
                conn.commit()
        except BaseException:
            _remove(path)
            raise
        return line_id

    def record_plays(self, plays):
        """Adds per-line play counts from a phone, then retires the most-played lines once a day per guest."""
        with self._lock, self.connect() as conn:
            for line_id, count in plays.items():
                conn.execute("UPDATE slowed_lines SET plays = plays + ? WHERE id = ?", (int(count), line_id))
            conn.commit()
        return self.retire_most_played()

    def retire_most_played(self):
        """Up to 20% of a guest's lines, most played first, once per day per guest. Returns the retired ids."""
        retired, today = [], self.today().isoformat()
        with self._lock, self.connect() as conn:
            for guest in GUESTS:
                row = conn.execute("SELECT day FROM slowed_retirements WHERE guest = ?", (guest,)).fetchone()
                if row and row[0] == today:
                    continue
                total = conn.execute("SELECT COUNT(*) FROM slowed_lines WHERE guest = ? AND retired IS NULL", (guest,)).fetchone()[0]
                limit = int(total * RETIRE_FRACTION)
                ids = [r[0] for r in conn.execute(
                    "SELECT id FROM slowed_lines WHERE guest = ? AND retired IS NULL AND plays > 0 "
                    "ORDER BY plays DESC, created LIMIT ?", (guest, limit))]
                if not ids:
                    continue
                revision = self.revision(conn) + 1
                for line_id in ids:
                    conn.execute("UPDATE slowed_lines SET retired = ?, retired_rev = ? WHERE id = ?", (self.wall(), revision, line_id))
                    _remove(os.path.join(self.directory, line_id + ".m4a"))
                conn.execute("INSERT OR REPLACE INTO slowed_retirements (guest, day) VALUES (?, ?)", (guest, today))
                conn.commit()
                retired += ids
        return retired


def _wav_duration_ms(path):
    from engine.playback_plan import _duration_ms
    return _duration_ms(path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# -- the background worker ----------------------------------------------------------

class SlowedLineWorker:
    """Keeps every guest's bank at its targets, one line at a time, only while the desktop is idle.

    Started on demand (a phone asking for the bank), so a desktop whose phones never use Slowed never spends GPU time.
    """

    def __init__(self, bank, *, idle=None, chat=None, speak=None, names=library_names, sleep=time.sleep, poll_s=60, gate=None):
        self.bank, self.idle = bank, idle or default_idle
        # Held for each LLM call and each voiced line, so the bank never speaks over a connected-talk turn.
        self.gate = gate
        self.chat, self.speak = chat or _chat, speak or voice
        self.names, self.sleep, self.poll_s = names, sleep, poll_s
        self._thread, self._lock, self._wake = None, threading.Lock(), threading.Event()
        self.last_error = None

    def wake(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="slowed-lines", daemon=True)
                self._thread.start()
        self._wake.set()

    def _run(self):
        while True:
            try:
                worked = self.fill_once()
            except Exception as exc:                       # noqa: BLE001 - keep trying later
                logger.exception("Slowed line generation failed")
                self.last_error, worked = str(exc), False
            if not worked:
                self._wake.wait(self.poll_s)
                self._wake.clear()

    def _gated(self):
        return _Gate(self.gate)

    def fill_once(self):
        """Fills the first gap with up to three batches. True when it made progress or more gaps remain."""
        gaps = self.bank.gaps()
        if not gaps or not self.idle():
            return False
        guest_id, kind, preset, missing = gaps[0]
        from announcer.personas import get_persona
        guest = get_persona(guest_id)
        if guest is None:
            return False
        names = self.names() if callable(self.names) else set(self.names)
        added = 0
        for _ in range(MAX_BATCHES_PER_GAP):
            if added >= missing or not self.idle():
                break
            existing = self.bank.texts(guest_id, kind, preset)
            prompt = BANK_PROMPT.format(
                guest_name=guest.name, guest_personality=guest.personality, count=min(MAX_BATCH, missing - added + 2),
                moment=MOMENT_TEXT[kind], preset_description=PRESETS[preset]["description"] if preset else
                "the music has been slowed down, lower and with an echo", min_words=WORDS[kind][0], max_words=WORDS[kind][1],
                cue_rule=CUE_RULE[kind], existing_openings="; ".join(sorted({opening(t, 3) for t in existing})) or "none yet")
            accepted = []
            with self._gated() as allowed:
                if not allowed:
                    break
                raw = self.chat(prompt)
            for text in _parse_lines(raw):
                reason = check_line(text, kind, existing=existing + accepted, names=names)
                if reason is None:
                    accepted.append(text)
                else:
                    logger.debug("Slowed bank line rejected (%s): %s", reason, text[:100])
            for text in accepted:
                if added >= missing or not self.idle():
                    break
                with self._gated() as allowed:
                    if not allowed:
                        break
                    wav = self.speak(text, guest_id)
                if not wav:
                    continue
                self.bank.add(guest_id, kind, preset, text, wav)
                added += 1
        return added > 0 or len(gaps) > 1


class _Gate:
    def __init__(self, lock):
        self.lock, self.held = lock, False

    def __enter__(self):
        self.held = self.lock is None or self.lock.acquire(blocking=False)
        return self.held

    def __exit__(self, *exc):
        if self.held and self.lock is not None:
            self.lock.release()
        return False


def default_idle():
    """No connected-talk turn generating and no ingest running."""
    try:
        import mobile_talk
        if mobile_talk.GENERATING.locked():
            return False
    except Exception:                                      # noqa: BLE001
        pass
    try:
        from ingest import pipeline
        if pipeline.get_ingest_progress().get("running"):
            return False
    except Exception:                                      # noqa: BLE001
        pass
    return True


__all__ = ["PRESETS", "GUESTS", "SlowedBank", "SlowedLineWorker", "check_line", "live_line", "targets"]
