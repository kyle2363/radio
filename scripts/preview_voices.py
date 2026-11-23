"""Render every radio voice and talk show to WAV files so they can be auditioned offline.

Runs the radio's own code — personas, speech direction, ``tts_say`` / VoiceManager,
the talk-segment generators and the playback assembly (``prepare_talk``: 1 s fade-in,
natural pauses between lines) — with the current settings read from ``data/radio.db``.

Safety:
  * The database is opened READ-ONLY; any write the radio code attempts fails instead
    of touching production.
  * All audio, the TTS cache and the talk debug log go to
    ``.test-runtime/voice_preview/<timestamp>/``; ``data/tts_cache`` is never used.
  * Nothing is played; no listening history is recorded.

Needs the same services the radio needs: the active TTS engine (e.g. the ZONOS3
server or OmniVoice GPU) and, for shows/announcements, the LLM chat server.
Start the desktop app (or those servers) first.

Examples (from the repo root):
  backend\\venv\\Scripts\\python -B scripts/preview_voices.py               # voices + shows
  backend\\venv\\Scripts\\python -B scripts/preview_voices.py voices --emotions
  backend\\venv\\Scripts\\python -B scripts/preview_voices.py shows --only caller_dave guest_hype
  backend\\venv\\Scripts\\python -B scripts/preview_voices.py shows --mood chill --include-disabled
  backend\\venv\\Scripts\\python -B scripts/preview_voices.py voices --engine piper --allow-fallback

Open ``index.html`` in the output folder to listen and read what was said.
"""

import argparse
import html
import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
START_DIR = os.getcwd()
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import config  # noqa: E402  (loads .env)
from db import database  # noqa: E402

SAMPLE_LINE = ("Hey there, thanks for tuning in. Here's one I've had stuck in my head all week... "
               "honestly, it just gets better every time you hear it.")
EMOTION_SAMPLES = ("neutral", "warm", "excited", "calm", "sad", "amused", "annoyed")
TRIGGERS = ("radio_intro", "song_intro", "mood_change", "skip_streak", "wildcard", "recovery")


# ---- isolation -------------------------------------------------------------

def _read_only_connection():
    conn = sqlite3.connect(f"file:{database.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def isolate(out_dir):
    """Point every write the speech code makes at ``out_dir`` and lock the DB read-only."""
    if not os.path.isfile(database.DB_PATH):
        sys.exit(f"No database at {database.DB_PATH}")
    database.get_connection = _read_only_connection
    cache = os.path.join(out_dir, "tts_cache")
    os.makedirs(cache, exist_ok=True)
    config.TTS_CACHE_DIR = cache
    config.TALK_DEBUG_LOG_PATH = os.path.join(out_dir, "talk_debug.log")
    config.TALK_SHOW_DEBUG = True
    from announcer import tts, voice_manager
    tts.TTS_CACHE_DIR = cache
    voice_manager.TTS_CACHE_DIR = cache
    return cache


class Recorder:
    """Wraps ``tts_say`` where the radio calls it, logging each line's text and direction."""

    def __init__(self):
        self.lines = []

    def wrap(self, real):
        def recorded(text, voice_id=None, *args, **kwargs):
            started = time.time()
            wav = real(text, voice_id, *args, **kwargs)
            direction = kwargs.get("direction")
            self.lines.append({
                "text": text, "voice": voice_id, "speed": kwargs.get("speed"),
                "trigger": kwargs.get("trigger_type"), "wav": wav,
                "direction": asdict(direction) if direction is not None else None,
                "seconds": round(time.time() - started, 1),
            })
            return wav
        return recorded

    def take(self):
        lines, self.lines = self.lines, []
        return lines


def setup_voice_manager(engine_override, allow_fallback):
    from announcer.voice_manager import get_voice_manager
    vm = get_voice_manager()
    vm.set_database(database)  # read-only: a persisted change would raise, not write
    vm.initialize()
    mode = database.get_setting("tts_voice_mode")
    if mode:
        vm._voice_mode = mode
    if engine_override:
        vm._active_engine_id = engine_override  # this run only; not persisted
    engine_id, engine = vm.get_active_engine()
    ready = engine.is_ready()
    print(f"TTS engine: {engine_id} ({'ready' if ready else 'NOT READY'}), "
          f"default voice: {vm.get_voice() or '-'}, voice mode: {vm.get_voice_mode()}")
    if not ready and not allow_fallback:
        sys.exit(f"The {engine_id} engine is not ready, so the radio would fall back to Piper. "
                 "Start it (e.g. open the desktop app) or pass --allow-fallback.")
    return vm, engine_id


def print_flags():
    from announcer import speech_direction
    flags = speech_direction.current_flags()
    print("Speech direction:", ", ".join(f"{k}={v}" for k, v in asdict(flags).items()))
    try:
        speed = database.get_setting("dj_speech_speed")
        print(f"dj_speech_speed: {speed or '1.0 (default)'}")
    except Exception:
        pass


def llm_available():
    """One tiny request to the same chat client the shows use."""
    from llm import llm_chat
    try:
        reply = llm_chat().chat(model=None, messages=[{"role": "user", "content": "Reply with OK."}],
                                options=config.OLLAMA_CHAT_OPTIONS, think=False)
        return bool((reply.content or "").strip())
    except Exception as e:
        print(f"LLM check failed: {e}")
        return False


# ---- song context ----------------------------------------------------------

def random_song():
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT id, artist, title, energy, valence FROM songs "
            "WHERE title IS NOT NULL AND title != '' ORDER BY RANDOM() LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        return {"artist": "Unknown", "title": "Unknown"}
    return {"song_id": row["id"], "artist": row["artist"] or "", "title": row["title"] or "",
            "energy": row["energy"], "valence": row["valence"]}


# ---- renderers -------------------------------------------------------------

def assemble_like_playback(wavs):
    """Exactly what the desktop queue does before playing a talk segment."""
    from announcer.voice_manager import _concatenate_wavs, _fade_in_first_second
    from engine.playback_plan import prepare_talk
    import threading

    class _Queue:  # the bits of QueueManager that prepare_talk reads
        _condition = threading.Condition()
        _queue = []
        _current_session_id = None

    plan = prepare_talk(_Queue(), {"wav_paths": wavs}, _fade_in_first_second, _concatenate_wavs)
    return plan.items[0].filepath


def persona_list(include_disabled, only):
    from announcer.personas import PERSONAS
    personas = list(PERSONAS.values())
    if only:
        personas = [p for p in personas if p.id in only]
    elif not include_disabled:
        personas = [p for p in personas if not p.disabled_by_default]
    return personas


def render_voices(args, vm, recorder, results):
    from announcer import speech_direction
    from announcer.tts import tts_say
    say = recorder.wrap(tts_say)
    flags = speech_direction.current_flags()
    hour = datetime.now().hour
    for persona in persona_list(args.include_disabled, args.only):
        emotions = EMOTION_SAMPLES if args.emotions else (None,)
        for emotion in emotions:
            label = f"{persona.name} ({persona.id})" + (f" - {emotion}" if emotion else "")
            print(f"  voice: {label} -> {persona.voice_id} @ {persona.speed_base}")
            text = speech_direction.prepare_line(args.text, flags, rng=random.Random(1),
                                                 mark_probability=0.0)
            direction = speech_direction.direction_for_line(
                emotion, mood_state=args.mood, persona_id=persona.id,
                context={"hour": hour}, flags=flags)
            wav = say(text, persona.voice_id, speed=persona.speed_base, direction=direction)
            results.append({"section": "Voices", "title": label, "wav": wav,
                            "lines": recorder.take()})

    if args.all_clips:
        dj = persona_list(True, ["dj"])[0]
        for voice in vm.get_available_voices():
            print(f"  clip: {voice}")
            direction = speech_direction.direction_for_line(None, mood_state=args.mood,
                                                            context={"hour": hour}, flags=flags)
            wav = say(args.text, voice, speed=dj.speed_base, direction=direction)
            results.append({"section": "Reference clips", "title": voice, "wav": wav,
                            "lines": recorder.take()})


def render_shows(args, recorder, results):
    from announcer import announcements, talk_segments
    from announcer.personas import get_caller_personas, get_guest_personas
    talk_segments.tts_say = recorder.wrap(talk_segments.tts_say)
    announcements.tts_say = recorder.wrap(announcements.tts_say)

    def keep(segment_or_wav, section, title):
        lines = recorder.take()
        wavs = [line["wav"] for line in lines if line["wav"]]
        if not segment_or_wav or not wavs:
            print(f"    FAILED: {title} (is the LLM chat server running? see talk_debug.log)")
            results.append({"section": section, "title": title + " (failed)", "wav": None,
                            "lines": lines})
            return
        final = assemble_like_playback(wavs)
        results.append({"section": section, "title": title, "wav": final, "lines": lines})

    callers = [c for c in get_caller_personas()
               if (args.include_disabled or not c.disabled_by_default)
               and (not args.only or c.id in args.only)]
    for caller in callers:
        print(f"  caller show: {caller.name}")
        talk_segments._pick_caller = lambda cooldown=0, c=caller: c
        segment = talk_segments.generate_caller_segment(
            mood_state=args.mood, next_song=random_song(),
            session_state={"hour": datetime.now().hour, "skip_streak": 0,
                           "mood_changed_recently": False, "session_duration_minutes": 30})
        keep(segment, "Caller shows", f"{caller.name} calls in")

    if not args.only or "dj" in args.only:
        for n in range(args.monologues):
            print(f"  DJ monologue {n + 1}/{args.monologues}")
            segment = talk_segments.generate_dj_monologue(mood_state=args.mood, next_song=random_song())
            kind = segment.reason if segment else "DJ monologue"
            keep(segment, "DJ monologues", kind)

    for guest in get_guest_personas():
        if args.only and guest.id not in args.only:
            continue
        print(f"  guest takeover: {guest.name}")
        upcoming = [random_song() for _ in range(3)]
        intro = talk_segments.generate_guest_takeover_intro(
            guest.id, last_song=random_song(), upcoming_songs=upcoming,
            mood_state=args.mood, next_song=upcoming[0])
        keep(intro, "Guest takeovers", f"{guest.name} intro")
        outro = talk_segments.generate_guest_takeover_outro(guest.id, played_songs=upcoming)
        keep(outro, "Guest takeovers", f"{guest.name} outro")

    if not args.only or "dj" in args.only:
        for trigger in TRIGGERS:
            print(f"  announcement: {trigger}")
            announcements._last_announcement_time = 0  # bypass the anti-spam interval
            context = {"current_song": random_song(), "next_song": random_song(),
                       "mood_state": args.mood, "skip_streak": 3, "hour": datetime.now().hour}
            text = announcements.get_announcement(
                context, announcements.AnnounceTrigger(trigger), advance=False)
            wav = announcements.play_announcement(text, "", trigger_type=trigger,
                                                  context=context) if text else None
            lines = recorder.take()
            results.append({"section": "DJ announcements", "title": trigger, "wav": wav,
                            "lines": lines or [{"text": text or "(no text)"}]})


# ---- report ----------------------------------------------------------------

def write_index(out_dir, results, header):
    sections = {}
    for item in results:
        sections.setdefault(item["section"], []).append(item)
    parts = [f"<!doctype html><meta charset=utf-8><title>Voice preview</title>"
             "<style>body{font:14px system-ui;max-width:900px;margin:24px auto;padding:0 16px;"
             "background:#111;color:#ddd}h2{border-bottom:1px solid #444}audio{width:100%}"
             ".item{margin:14px 0;padding:10px;background:#1b1b1b;border-radius:8px}"
             "li{margin:4px 0}small{color:#999}</style>",
             f"<h1>Voice preview</h1><p><small>{html.escape(header)}</small></p>"]
    for section, items in sections.items():
        parts.append(f"<h2>{html.escape(section)}</h2>")
        for item in items:
            parts.append(f"<div class=item><b>{html.escape(item['title'])}</b>")
            if item["wav"]:
                rel = os.path.relpath(item["wav"], out_dir).replace("\\", "/")
                parts.append(f"<audio controls preload=none src=\"{html.escape(rel)}\"></audio>")
            parts.append("<ol>")
            for line in item["lines"]:
                d = line.get("direction") or {}
                meta = ", ".join(f"{k}={v}" for k, v in (
                    ("voice", line.get("voice")), ("speed", line.get("speed")),
                    ("emotion", d.get("emotion")), ("energy", d.get("energy")),
                    ("pace", d.get("pace")), ("traits", d.get("traits")),
                    ("synth s", line.get("seconds"))) if v not in (None, 0, 0.0))
                parts.append(f"<li>{html.escape(line.get('text') or '')}<br><small>"
                             f"{html.escape(meta)}</small></li>")
            parts.append("</ol></div>")
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", nargs="?", choices=("all", "voices", "shows"), default="all")
    parser.add_argument("--only", nargs="+", help="persona ids, e.g. dj caller_dave guest_hype")
    parser.add_argument("--include-disabled", action="store_true", help="also disabled-by-default callers")
    parser.add_argument("--emotions", action="store_true", help="each voice in several emotions")
    parser.add_argument("--all-clips", action="store_true", help="also every reference clip the engine has")
    parser.add_argument("--text", default=SAMPLE_LINE, help="line used for voice samples")
    parser.add_argument("--mood", default="neutral",
                        choices=("neutral", "chill", "energetic", "focus", "melancholy", "discovery"))
    parser.add_argument("--monologues", type=int, default=3)
    parser.add_argument("--engine", help="override the active TTS engine for this run (not saved)")
    parser.add_argument("--allow-fallback", action="store_true",
                        help="continue even if the engine is down (radio would use Piper)")
    parser.add_argument("--out", help="output folder (default .test-runtime/voice_preview/<time>)")
    args = parser.parse_args()

    out_dir = os.path.abspath(os.path.join(START_DIR, args.out) if args.out else os.path.join(
        ROOT, ".test-runtime", "voice_preview", datetime.now().strftime("%Y%m%d-%H%M%S")))
    os.makedirs(out_dir, exist_ok=True)
    isolate(out_dir)
    if not config.TTS_ENABLED:
        sys.exit("TTS is disabled (RADIO_TTS_ENABLED); the radio would not speak.")

    vm, engine_id = setup_voice_manager(args.engine, args.allow_fallback)
    print_flags()
    print(f"Output: {out_dir}\n")

    recorder = Recorder()
    results = []
    if args.what in ("all", "voices"):
        print("Voices:")
        render_voices(args, vm, recorder, results)
    if args.what in ("all", "shows"):
        if llm_available():
            print("Shows:")
            render_shows(args, recorder, results)
        else:
            print("Shows skipped: the LLM chat server did not answer (shows are written by it). "
                  "Start the desktop app or llama.cpp chat and run again.")

    for item in results:  # an engine can 'succeed' with an empty file
        if item["wav"] and (not os.path.isfile(item["wav"]) or os.path.getsize(item["wav"]) < 1000):
            item["title"] += " (empty audio)"
            item["wav"] = None
    header = (f"{datetime.now():%Y-%m-%d %H:%M} | engine {engine_id} | mood {args.mood} | "
              f"{len(results)} items, {sum(1 for r in results if not r['wav'])} failed")
    index = write_index(out_dir, results, header)
    print(f"\n{header}\nOpen: {index}")


if __name__ == "__main__":
    main()
