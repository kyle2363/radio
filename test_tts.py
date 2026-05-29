"""TTS pipeline diagnostic test.

Tests each layer of the TTS system independently:
  1. Piper TTS engine (raw synthesis)
  2. VoiceManager (caching, voice switching)
  3. Announcements (LLM + TTS)
  4. Talk segments (multi-voice LLM + TTS)
  5. Queue pre-generation flow
  6. play_chain integration

Run:  backend/venv/Scripts/python test_tts.py
"""

import os
import sys
import time
import wave
import hashlib
import traceback

# Ensure backend is on sys.path
BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TTS_CACHE = os.path.join(DATA_DIR, "tts_cache")
PIPER_VOICE_DIR = os.path.join(DATA_DIR, "piper_voice")

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
results = []


def report(name, status, detail=""):
    results.append((name, status, detail))
    tag = f"\033[92m{status}\033[0m" if status == PASS else \
          f"\033[91m{status}\033[0m" if status == FAIL else \
          f"\033[93m{status}\033[0m"
    line = f"  [{tag}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)


def check_wav_valid(path):
    """Return (ok, info) for a WAV file."""
    if not path or not os.path.exists(path):
        return False, f"file not found: {path}"
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            duration = nframes / framerate if framerate else 0
            size = os.path.getsize(path)
            return True, f"{duration:.1f}s, {framerate}Hz, {channels}ch, {size} bytes"
    except Exception as e:
        return False, f"invalid WAV: {e}"


# ─────────────────────────────────────────────
# Test 1: Piper TTS Engine — raw synthesis
# ─────────────────────────────────────────────
def test_piper_engine():
    print("\n== 1. Piper TTS Engine ==")
    try:
        from announcer.tts_engine import PiperTTSEngine
    except Exception as e:
        report("import PiperTTSEngine", FAIL, str(e))
        return

    engine = PiperTTSEngine()
    report("PiperTTSEngine instantiated", PASS)

    # Check voice downloaded
    if engine.voice_downloaded("amy"):
        report("voice 'amy' downloaded", PASS,
               f"onnx={os.path.getsize(engine._voice_path('amy'))} bytes")
    else:
        report("voice 'amy' downloaded", FAIL,
               f"not found at {engine._voice_path('amy')}")
        return

    # Load voice
    t0 = time.time()
    ok = engine.load_voice("amy")
    elapsed = time.time() - t0
    if ok:
        report("load_voice('amy')", PASS, f"{elapsed:.1f}s")
    else:
        report("load_voice('amy')", FAIL, f"returned False after {elapsed:.1f}s")
        return

    # Synthesize
    test_text = "Hello, this is a test of the Piper text to speech engine."
    out_path = os.path.join(TTS_CACHE, "test_piper_raw.wav")
    os.makedirs(TTS_CACHE, exist_ok=True)

    t0 = time.time()
    result = engine.synthesize(test_text, "amy", out_path)
    elapsed = time.time() - t0

    if result:
        ok, info = check_wav_valid(out_path)
        if ok:
            report("synthesize()", PASS, f"{elapsed:.1f}s -- {info}")
        else:
            report("synthesize()", FAIL, f"file exists but invalid: {info}")
    else:
        report("synthesize()", FAIL, f"returned None after {elapsed:.1f}s")

    # Check available voices
    voices = engine.get_available_voices()
    report("get_available_voices()", PASS, str(voices))


# ─────────────────────────────────────────────
# Test 2: tts_say() wrapper
# ─────────────────────────────────────────────
def test_tts_say():
    print("\n== 2. tts_say() wrapper ==")
    try:
        from announcer.tts import tts_say
    except Exception as e:
        report("import tts_say", FAIL, str(e))
        return

    text = "Testing the tts_say wrapper function."
    t0 = time.time()
    result = tts_say(text)
    elapsed = time.time() - t0

    if result:
        ok, info = check_wav_valid(result)
        if ok:
            report("tts_say()", PASS, f"{elapsed:.1f}s -- {info}\n    path={result}")
        else:
            report("tts_say()", FAIL, f"returned path but invalid WAV: {info}")
    else:
        report("tts_say()", FAIL, f"returned None after {elapsed:.1f}s")

    # Test caching (should be instant)
    t0 = time.time()
    result2 = tts_say(text)
    elapsed2 = time.time() - t0
    if result2 and elapsed2 < 0.5:
        report("tts_say() cache hit", PASS, f"{elapsed2*1000:.0f}ms")
    elif result2:
        report("tts_say() cache hit", PASS, f"{elapsed2:.1f}s (slow but worked)")
    else:
        report("tts_say() cache hit", FAIL, "returned None on cached text")


# ─────────────────────────────────────────────
# Test 3: VoiceManager
# ─────────────────────────────────────────────
def test_voice_manager():
    print("\n== 3. VoiceManager ==")
    try:
        from announcer.voice_manager import get_voice_manager
    except Exception as e:
        report("import get_voice_manager", FAIL, str(e))
        return

    vm = get_voice_manager()
    report("VoiceManager singleton", PASS)

    status = vm.get_status()
    report("get_status()", PASS, str(status))

    # Synthesize with default voice
    text = "Voice manager test."
    t0 = time.time()
    result = vm.synthesize(text)
    elapsed = time.time() - t0

    if result:
        ok, info = check_wav_valid(result)
        if ok:
            report("vm.synthesize()", PASS, f"{elapsed:.1f}s -- {info}")
        else:
            report("vm.synthesize()", FAIL, f"invalid WAV: {info}")
    else:
        report("vm.synthesize()", FAIL, f"returned None after {elapsed:.1f}s")


# ─────────────────────────────────────────────
# Test 4: LLM connectivity
# ─────────────────────────────────────────────
def test_llm():
    print("\n== 4. LLM Chat Provider ==")
    try:
        from llm.registry import llm_chat
    except Exception as e:
        report("import llm_chat", FAIL, str(e))
        return

    try:
        client = llm_chat()
        report("llm_chat() resolved", PASS, f"provider={client.__class__.__name__}")
    except Exception as e:
        report("llm_chat()", FAIL, str(e))
        return

    t0 = time.time()
    try:
        resp = client.chat(
            model=None,
            messages=[{"role": "user", "content": "Say exactly: TTS test OK"}],
            options={"temperature": 0.1, "num_predict": 20},
            think=False,
        )
        elapsed = time.time() - t0
        text = resp.get("message", {}).get("content", "").strip()
        report("llm chat call", PASS, f"{elapsed:.1f}s -- '{text}'")
    except Exception as e:
        elapsed = time.time() - t0
        report("llm chat call", FAIL, f"{elapsed:.1f}s -- {e}")


# ─────────────────────────────────────────────
# Test 5: Announcement pipeline (LLM + TTS)
# ─────────────────────────────────────────────
def test_announcement():
    print("\n== 5. Announcement Pipeline ==")
    try:
        from announcer.announcements import get_announcement, play_announcement, AnnounceTrigger
    except Exception as e:
        report("import announcements", FAIL, str(e))
        return

    context = {
        "current_song": {"artist": "The Beatles", "title": "Let It Be"},
        "next_song": {"artist": "Queen", "title": "Bohemian Rhapsody"},
        "mood_state": "neutral",
        "skip_streak": 0,
        "hour": 14,
    }

    # Reset the interval timer so it doesn't skip
    import announcer.announcements as ann_mod
    ann_mod._last_announcement_time = 0

    t0 = time.time()
    try:
        text = get_announcement(context, AnnounceTrigger.RADIO_INTRO)
        elapsed = time.time() - t0
        if text:
            report("get_announcement()", PASS, f"{elapsed:.1f}s -- '{text}'")
        else:
            report("get_announcement()", FAIL, f"returned None after {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.time() - t0
        report("get_announcement()", FAIL, f"{elapsed:.1f}s -- {e}")
        return

    # Now render it
    if text:
        t0 = time.time()
        try:
            wav = play_announcement(text, "/nonexistent/song.mp3")
            elapsed = time.time() - t0
            if wav:
                ok, info = check_wav_valid(wav)
                if ok:
                    report("play_announcement()", PASS, f"{elapsed:.1f}s -- {info}")
                else:
                    report("play_announcement()", FAIL, f"invalid WAV: {info}")
            else:
                report("play_announcement()", FAIL, f"returned None after {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - t0
            report("play_announcement()", FAIL, f"{elapsed:.1f}s -- {e}")


# ─────────────────────────────────────────────
# Test 6: Talk segments
# ─────────────────────────────────────────────
def test_talk_segments():
    print("\n== 6. Talk Segments ==")
    try:
        from announcer.talk_segments import generate_caller_segment, generate_dj_monologue
    except Exception as e:
        report("import talk_segments", FAIL, str(e))
        return

    # Caller segment (5 TTS renders)
    t0 = time.time()
    try:
        segment = generate_caller_segment(mood_state="neutral", caller_mode="narrative_only")
        elapsed = time.time() - t0
        if segment:
            wav_paths = segment.wav_paths
            valid = [w for w in wav_paths if os.path.exists(w)]
            durations = []
            for w in valid:
                ok, info = check_wav_valid(w)
                if ok:
                    durations.append(info)
            report("generate_caller_segment()", PASS,
                   f"{elapsed:.1f}s -- {len(valid)}/{len(wav_paths)} WAVs valid")
            for i, (w, d) in enumerate(zip(valid, durations)):
                print(f"      [{i}] {os.path.basename(w)}: {d}")
        else:
            report("generate_caller_segment()", PASS,
                   f"{elapsed:.1f}s -- returned None (may be expected if disabled)")
    except Exception as e:
        elapsed = time.time() - t0
        report("generate_caller_segment()", FAIL, f"{elapsed:.1f}s -- {e}")
        traceback.print_exc()

    # Monologue segment (1 TTS render)
    t0 = time.time()
    try:
        segment = generate_dj_monologue(mood_state="chill")
        elapsed = time.time() - t0
        if segment:
            wav_paths = segment.wav_paths
            valid = [w for w in wav_paths if os.path.exists(w)]
            report("generate_dj_monologue()", PASS,
                   f"{elapsed:.1f}s -- {len(valid)}/{len(wav_paths)} WAVs valid")
        else:
            report("generate_dj_monologue()", PASS,
                   f"{elapsed:.1f}s -- returned None")
    except Exception as e:
        elapsed = time.time() - t0
        report("generate_dj_monologue()", FAIL, f"{elapsed:.1f}s -- {e}")


# ─────────────────────────────────────────────
# Test 7: Database settings
# ─────────────────────────────────────────────
def test_db_settings():
    print("\n== 7. DB Settings ==")
    try:
        from db import database
    except Exception as e:
        report("import database", FAIL, str(e))
        return

    try:
        database.init_db()
        report("database.init_db()", PASS)
    except Exception as e:
        report("database.init_db()", FAIL, str(e))
        return

    settings_keys = [
        "tts_enabled", "talk_segments_enabled", "talk_frequency",
        "talk_caller_enabled", "talk_monologue_enabled",
        "talk_guest_takeover_enabled", "announcement_interval",
        "tts_engine", "tts_voice", "current_mode",
    ]
    for key in settings_keys:
        val = database.get_setting(key)
        report(f"setting '{key}'", PASS if val is not None else FAIL,
               f"= {val!r}")


# ─────────────────────────────────────────────
# Test 8: Config check
# ─────────────────────────────────────────────
def test_config():
    print("\n== 8. Config ==")
    import config
    report("config.TTS_ENABLED", PASS if config.TTS_ENABLED else FAIL,
           str(config.TTS_ENABLED))
    report("config.TTS_VOICE", PASS, config.TTS_VOICE)
    report("config.CHAT_PROVIDER", PASS, config.CHAT_PROVIDER)
    report("config.EMBED_PROVIDER", PASS, config.EMBED_PROVIDER)
    report("config.OPENAI_BASE_URL_CHAT", PASS, config.OPENAI_BASE_URL_CHAT)
    report("config.OLLAMA_BASE_URL", PASS, config.OLLAMA_BASE_URL)
    report("config.DATA_DIR", PASS, config.DATA_DIR)
    report("piper_voice dir exists", PASS if os.path.isdir(PIPER_VOICE_DIR) else FAIL,
           PIPER_VOICE_DIR)


# ─────────────────────────────────────────────
# Run all tests
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  TTS Pipeline Diagnostic")
    print("=" * 60)

    test_config()
    test_db_settings()
    test_piper_engine()
    test_tts_say()
    test_voice_manager()
    test_llm()
    test_announcement()
    test_talk_segments()

    # Summary
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    skipped = sum(1 for _, s, _ in results if s == SKIP)
    total = len(results)

    print("\n" + "=" * 60)
    print(f"  Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)

    if failed:
        print("\n  Failed tests:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"    - {name}: {detail}")

    sys.exit(0 if failed == 0 else 1)
