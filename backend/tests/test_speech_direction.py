"""Engine-neutral speech direction: settings, marks, builders, adapters and the voice manager path."""
import random
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from announcer import speech_direction as sd
from announcer import voice_manager, zonos3_direction
from db import database


# ---- settings ----

def test_every_feature_has_a_seeded_setting_and_is_on_by_default():
    for key, default in sd.SETTING_DEFAULTS.items():
        assert database.get_setting(key) == default, key
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    assert flags.enabled and flags.emotion and flags.mood_delivery and flags.loudness_match
    assert flags.tight_start and flags.word_marks and flags.pauses and flags.persona_traits
    assert flags.loudness_target == pytest.approx(-16.0)


def test_master_switch_turns_every_feature_off():
    database.set_setting("speech_direction_enabled", "false")
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    assert not any([flags.emotion, flags.mood_delivery, flags.loudness_match, flags.tight_start,
                    flags.word_marks, flags.pauses, flags.persona_traits])


def test_features_turn_off_one_at_a_time_and_bad_values_fall_back():
    database.set_setting("speech_pauses_enabled", "false")
    database.set_setting("speech_loudness_target_lufs", "loud please")
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    assert flags.enabled and not flags.pauses and flags.word_marks
    assert flags.loudness_target == pytest.approx(-16.0)


# ---- inline marks ----

def test_marks_become_character_spans_over_plain_text():
    marked = sd.parse_marks("So <whisper>between you and me</whisper>, that was <shout>huge</shout>!")
    assert marked.text == "So between you and me, that was huge!"
    whisper, shout = marked.spans
    assert marked.text[whisper.start:whisper.end] == "between you and me" and whisper.style == "whisper"
    assert marked.text[shout.start:shout.end] == "huge" and shout.style == "shout"


def test_nonverbal_tags_become_spans_over_their_sentence():
    marked = sd.parse_marks("[laughter] That cracked me up. Anyway.")
    assert marked.text == "That cracked me up. Anyway."
    [span] = marked.spans
    assert span.style == "laughter"
    assert marked.text[span.start:span.end] == "That cracked me up."


def test_unclosed_and_unknown_marks_never_reach_the_speech_text():
    marked = sd.parse_marks("A <soft>quiet ending and <bogus>tags</bogus>")
    assert "<" not in marked.text and ">" not in marked.text
    assert marked.text == "A quiet ending and tags"
    [soft] = marked.spans
    assert soft.style == "soft" and soft.end == len(marked.text)


def test_strip_marks_keeps_tags_only_when_asked():
    text = "[sigh] Those <pause> were <emph>the</emph> days."
    assert sd.strip_marks(text) == "Those were the days."
    assert sd.strip_marks(text, keep_tags=True) == "[sigh] Those were the days."


def test_pauses_split_text_into_chunks_with_silence_after_each():
    chunks = sd.split_pauses("Well <pause> I suppose <pause long> it's late.")
    assert [c for c, _ in chunks] == ["Well", "I suppose", "it's late."]
    assert chunks[0][1] < chunks[1][1] and chunks[2][1] == 0


def test_mark_pauses_turns_trailing_dots_and_dashes_into_pauses_but_caps_them():
    text = "Well... I suppose — maybe... it's late... really... yes."
    marked = sd.mark_pauses(text)
    assert marked.count("<pause") == sd.MAX_PAUSES_PER_LINE
    assert sd.strip_marks(marked).replace("  ", " ")


def test_word_marks_are_bounded_and_deterministic_with_a_seed():
    text = "No way! Between you and me, I never liked it. Those were the days."
    marked = sd.add_word_marks(text, rng=random.Random(3), probability=1.0)
    assert "<whisper>" in marked and sd.strip_marks(marked) == text
    assert sum(marked.count(f"<{s}>") for s in sd.SPAN_STYLES) <= sd.MAX_MARKS_PER_LINE


# ---- builders ----

def test_announcement_trigger_sets_an_emotion_and_mood_sets_delivery():
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    chill = sd.direction_for_announcement("skip_streak", {"mood_state": "chill", "hour": 14}, flags=flags)
    hype = sd.direction_for_announcement("mood_change", {"mood_state": "energetic", "hour": 14}, flags=flags)
    assert chill.emotion == "calm" and hype.emotion == "excited"
    assert chill.energy < 0 < hype.energy
    assert chill.loudness_lufs == pytest.approx(-16.0) and chill.tight_start


def test_next_song_energy_and_late_hours_shape_the_intro():
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    loud = sd.direction_for_announcement("radio_intro", {"mood_state": "neutral", "hour": 14,
                                                         "next_song": {"energy": 0.95, "valence": 0.9}}, flags=flags)
    night = sd.direction_for_announcement("radio_intro", {"mood_state": "neutral", "hour": 2,
                                                          "next_song": {"energy": 0.95, "valence": 0.9}}, flags=flags)
    assert loud.energy > night.energy and loud.valence > 0


def test_disabled_features_leave_the_direction_neutral():
    database.set_setting("speech_direction_enabled", "false")
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    direction = sd.direction_for_announcement("mood_change", {"mood_state": "energetic"}, flags=flags)
    assert direction.is_neutral


def test_line_emotions_are_normalized_and_unknown_words_are_ignored():
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    assert sd.direction_for_line("Amused!", flags=flags).emotion == "amused"
    assert sd.direction_for_line("flabbergasted", flags=flags).emotion is None


def test_persona_traits_follow_their_setting():
    flags = sd.DirectionFlags.from_settings(database.get_setting)
    assert sd.direction_for_line(None, persona_id="caller_ed", flags=flags).traits.accent == "british"
    database.set_setting("speech_persona_traits_enabled", "false")
    off = sd.DirectionFlags.from_settings(database.get_setting)
    assert sd.direction_for_line(None, persona_id="caller_ed", flags=off).traits is None


# ---- adapters ----

def test_unknown_engines_get_plain_text_and_no_request_fields():
    adapter = sd.get_adapter("some-future-model")
    rendered = adapter.render("[laughter] Ha <whisper>quiet</whisper> <pause> done", sd.SpeechDirection(emotion="happy"))
    assert rendered.text == "Ha quiet done" and rendered.request == {}


def test_omnivoice_keeps_its_tags_and_native_pauses():
    adapter = sd.get_adapter("omnivoice")
    rendered = adapter.render("[laughter] Ha <whisper>quiet</whisper> <pause> done", sd.SpeechDirection())
    assert rendered.text == "[laughter] Ha quiet .,. done"
    assert adapter.native_pauses and rendered.request == {}


def test_zonos3_maps_direction_to_controls_spans_loudness_and_tight_start():
    adapter = sd.get_adapter("zonos3")
    assert isinstance(adapter, zonos3_direction.Zonos3Direction)
    direction = sd.SpeechDirection(emotion="excited", intensity=1.0, energy=1.0, valence=1.0, loudness_lufs=-16.0,
                                   tight_start=True, traits=sd.VoiceTraits(accent="british", textures=(("deep", 1.0),)))
    rendered = adapter.render("Wow <shout>yes</shout> [sigh] Long day.", direction)
    assert rendered.text == "Wow yes Long day."
    req = rendered.request
    assert req["controls"]["mood.arousal"] > 0 and req["controls"]["accents_en.british"] > 0
    assert req["controls"]["texture.deep"] > 0
    assert req["condition_values"] == {"loudness": -16.0}
    assert req["silence_prefill_frames"] == 0
    shout, sigh = req["control_spans"]
    assert rendered.text[shout["start"]:shout["end"]] == "yes" and shout["controls"]["delivery.loud"] > 0
    assert rendered.text[sigh["start"]:sigh["end"]] == "Long day."


def test_zonos3_doses_stay_inside_the_studio_safe_ranges():
    adapter = sd.get_adapter("zonos3")
    extreme = sd.SpeechDirection(emotion="angry", intensity=5.0, energy=9, valence=-9, pace=9,
                                 traits=sd.VoiceTraits(accent="irish", accent_strength=9,
                                                       textures=tuple((t, 9.0) for t in zonos3_direction.TEXTURES)),
                                 loudness_lufs=-16)
    marked = " ".join(f"<{s}>x</{s}>" for s in sd.SPAN_STYLES) + " [laughter] y. [sigh] z."
    req = adapter.render(marked, extreme).request
    doses = dict(req["controls"])
    for span in req["control_spans"]:
        for cid, dose in span["controls"].items():
            doses[cid] = max(abs(doses.get(cid, 0)), abs(dose))
    for cid, dose in doses.items():
        low, high = zonos3_direction.SAFE[cid]
        assert low <= dose <= high, (cid, dose)


def test_zonos3_neutral_direction_sends_nothing_extra():
    rendered = sd.get_adapter("zonos3").render("Just words.", sd.SpeechDirection())
    assert rendered.text == "Just words." and rendered.request == {}


# ---- voice manager ----

class RecordingEngine:
    synthesis_timeout = 30

    def __init__(self, level=0.05):
        self.calls = []
        self.level = level

    def is_ready(self):
        return True

    def synthesize(self, text, voice, path, speed=None, controls=None, request=None):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "request": request})
        t = np.linspace(0, 1.0, 22050, endpoint=False)
        sf.write(path, (self.level * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 22050)
        return path


@pytest.fixture
def manager(monkeypatch):
    m = voice_manager.VoiceManager()
    monkeypatch.setattr(voice_manager, "run_with_timeout", lambda fn, timeout: (True, fn()))
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    monkeypatch.setattr(voice_manager, "_prepend_silence", lambda path: None)
    return m


def use_engine(monkeypatch, m, engine_id, engine):
    monkeypatch.setattr(m, "get_active_engine", lambda: (engine_id, engine))


def loudness(path):
    import pyloudnorm
    data, rate = sf.read(path)
    return pyloudnorm.Meter(rate).integrated_loudness(data)


def test_zonos3_receives_rendered_request_and_plain_text(monkeypatch, tmp_path, manager):
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    direction = sd.SpeechDirection(emotion="happy", tight_start=True)
    assert manager.synthesize("Hi <emph>there</emph>.", "Host", str(tmp_path), speed=0.9, direction=direction)
    [call] = engine.calls
    assert call["text"] == "Hi there."
    assert call["request"]["controls"]["emotions.happy"] > 0
    assert call["request"]["control_spans"][0]["start"] == 3


def test_direction_changes_the_cache_key(monkeypatch, tmp_path, manager):
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    a = manager.synthesize("Same words.", "Host", str(tmp_path), direction=sd.SpeechDirection(emotion="happy"))
    b = manager.synthesize("Same words.", "Host", str(tmp_path), direction=sd.SpeechDirection(emotion="sad"))
    assert a != b and len(engine.calls) == 2


def test_pauses_split_synthesis_for_engines_without_native_pauses(monkeypatch, tmp_path, manager):
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    path = manager.synthesize("Well <pause> it's late.", "Host", str(tmp_path), direction=sd.SpeechDirection(pauses=True))
    assert [c["text"] for c in engine.calls] == ["Well", "it's late."]
    data, rate = sf.read(path)
    assert len(data) / rate > 2.2  # two one-second chunks plus the pause


def test_pause_marks_are_removed_when_pauses_are_off(monkeypatch, tmp_path, manager):
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    manager.synthesize("Well <pause> it's late.", "Host", str(tmp_path), direction=sd.SpeechDirection())
    assert [c["text"] for c in engine.calls] == ["Well it's late."]


def test_loudness_is_matched_for_any_engine(monkeypatch, tmp_path, manager):
    quiet, loud = RecordingEngine(level=0.03), RecordingEngine(level=0.5)
    direction = sd.SpeechDirection(loudness_lufs=-16.0)
    use_engine(monkeypatch, manager, "future", quiet)
    monkeypatch.setattr(voice_manager.sd, "get_adapter", lambda eid: sd.PlainAdapter())
    a = manager.synthesize("Quiet voice.", "A", str(tmp_path), direction=direction)
    use_engine(monkeypatch, manager, "future", loud)
    b = manager.synthesize("Loud voice.", "B", str(tmp_path), direction=direction)
    assert loudness(a) == pytest.approx(-16.0, abs=1.0)
    assert loudness(b) == pytest.approx(-16.0, abs=1.0)
    assert max(abs(sf.read(b)[0])) < 1.0


def test_without_direction_old_callers_still_get_trigger_direction(monkeypatch, tmp_path, manager):
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    manager.synthesize("Locked in.", "Host", str(tmp_path), trigger_type="vibe_lock")
    [call] = engine.calls
    assert call["request"]["controls"]["emotions.happy"] > 0


def test_master_off_sends_plain_requests(monkeypatch, tmp_path, manager):
    database.set_setting("speech_direction_enabled", "false")
    engine = RecordingEngine()
    use_engine(monkeypatch, manager, "zonos3", engine)
    manager.synthesize("Hi <emph>there</emph> <pause> now.", "Host", str(tmp_path), trigger_type="vibe_lock",
                       direction=sd.SpeechDirection(emotion="happy", pauses=True, loudness_lufs=-16))
    [call] = engine.calls
    assert call["text"] == "Hi there now." and not call["request"]


def test_omnivoice_path_is_unchanged_without_direction(monkeypatch, tmp_path, manager):
    calls = []

    class Omni:
        synthesis_timeout = 30

        def is_ready(self):
            return True

        def synthesize(self, text, voice, path, speed=None):
            calls.append(text)
            Path(path).write_bytes(b"wav")
            return path

    use_engine(monkeypatch, manager, "omnivoice", Omni())
    manager.synthesize("[laughter] Hello <pause> there.", "Host", str(tmp_path))
    assert calls == ["... [laughter] Hello .,. there."]


# ---- talk show and announcements ----

import json
from types import SimpleNamespace

from announcer import announcements, talk_segments


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def __call__(self):
        return self

    def chat(self, model=None, messages=None, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return SimpleNamespace(content=self.content)


@pytest.fixture
def said(monkeypatch, tmp_path):
    calls = []

    def fake_tts(text, voice_id=None, output_dir=None, song_id=None, trigger_type=None, speed=None, direction=None):
        calls.append({"text": text, "voice": voice_id, "direction": direction})
        path = tmp_path / f"line-{len(calls)}.wav"
        path.write_bytes(b"wav")
        return str(path)

    for module in (talk_segments, announcements):
        monkeypatch.setattr(module, "tts_say", fake_tts)
    monkeypatch.setattr(announcements.config, "TTS_ENABLED", True)
    return calls


def caller_json(rounds=2, emotions=None):
    data = {}
    for i in range(1, rounds + 1):
        data[f"dj_line_{i}"] = f"DJ line {i}. Between you and me, it was great."
        data[f"caller_line_{i}"] = f"Caller line {i}... no way!"
    data.update(dj_closer="Stay tuned.", mood_shift=None, energy_direction=None)
    if emotions is not None:
        data["emotions"] = emotions
    return json.dumps(data)


def test_caller_lines_carry_llm_emotions_mood_and_persona(monkeypatch, said):
    llm = FakeLLM(caller_json(emotions={"caller_line_1": "nervous", "dj_line_1": "warm", "dj_closer": "bogus"}))
    monkeypatch.setattr(talk_segments, "llm_chat", llm)
    monkeypatch.setattr(talk_segments.random, "choices", lambda seq, weights=None, k=1: [seq[0]])
    segment = talk_segments.generate_caller_segment(mood_state="chill")
    assert segment is not None
    assert "emotions" in llm.prompts[0]
    by_text = {c["text"]: c["direction"] for c in said}
    first_caller = next(d for t, d in by_text.items() if "Caller line 1" in sd.strip_marks(t))
    first_dj = next(d for t, d in by_text.items() if "DJ line 1" in sd.strip_marks(t))
    closer = next(d for t, d in by_text.items() if "Stay tuned" in t)
    assert first_caller.emotion == "nervous" and first_dj.emotion == "warm" and closer.emotion is None
    assert all(c["direction"].energy < 0 for c in said)  # chill mood
    assert all(c["direction"].loudness_lufs == pytest.approx(-16.0) for c in said)


def test_caller_emotion_prompt_and_marks_follow_their_settings(monkeypatch, said):
    database.set_setting("speech_emotion_enabled", "false")
    database.set_setting("speech_word_marks_enabled", "false")
    database.set_setting("speech_pauses_enabled", "false")
    llm = FakeLLM(caller_json(emotions={"caller_line_1": "nervous"}))
    monkeypatch.setattr(talk_segments, "llm_chat", llm)
    monkeypatch.setattr(talk_segments.random, "choices", lambda seq, weights=None, k=1: [seq[0]])
    talk_segments.generate_caller_segment(mood_state="neutral")
    assert "emotions" not in llm.prompts[0]
    assert all(c["direction"].emotion is None for c in said)
    assert not any("<whisper>" in c["text"] or "<pause" in c["text"] for c in said)


def test_monologue_gets_a_delivery_from_its_type(monkeypatch, said):
    monkeypatch.setattr(talk_segments, "llm_chat", FakeLLM("Late at night I think about this song."))
    talk_segments.generate_dj_monologue(mood_state="melancholy")
    [call] = said
    assert call["direction"].emotion in sd.EMOTIONS and call["direction"].energy < 0


def test_announcement_direction_uses_trigger_mood_and_next_song(said):
    announcements.play_announcement("Here comes a banger.", "", trigger_type="mood_change",
                                    context={"mood_state": "energetic", "hour": 15,
                                             "next_song": {"energy": 0.9, "valence": 0.8}})
    [call] = said
    assert call["direction"].emotion == "excited" and call["direction"].energy > 0.3
    assert call["direction"].loudness_lufs == pytest.approx(-16.0)


def test_marks_never_reach_the_segment_transcript(monkeypatch, said):
    monkeypatch.setattr(talk_segments, "llm_chat", FakeLLM("Between you and me... NO WAY, those were the days."))
    monkeypatch.setattr(sd, "add_word_marks", lambda text, rng=None, probability=0: "<whisper>" + text + "</whisper>")
    segment = talk_segments.generate_dj_monologue(mood_state="neutral")
    assert "<" not in segment.text and "[" not in segment.text
    assert "<whisper>" in said[0]["text"]
