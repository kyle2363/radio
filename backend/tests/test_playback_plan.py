"""Shared, reversible media preparation for desktop and remote output."""
from dataclasses import FrozenInstanceError
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import wave

import pytest

import config
from db import database
from tests.test_queue_lifecycle import queue_module, manager


@pytest.fixture
def media(manager, queue_module, monkeypatch):
    root = Path(config.DB_PATH).parent
    files = {}
    for name in ("song", "tts", "talk", "faded", "joined", "effect"):
        path = root / (name + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 8000)
        files[name] = str(path)
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET filepath=?, duration_secs=12.5, gain_db=-3 WHERE id=1", (files["song"],))
        conn.commit()
    calls = []
    manager._player = SimpleNamespace(
        play_song=lambda *args: calls.append(("song", args)) or True,
        play_chain=lambda *args, **kwargs: calls.append(("chain", args, kwargs)) or True,
        get_current_song_id=lambda: 2,
    )
    manager._current_song_id = 2
    manager._talk_context = SimpleNamespace(get_active_dj_effect_preset=lambda: "nightcore")
    manager._mood = SimpleNamespace(
        get_state=lambda: "neutral", set_state=lambda mood: calls.append(("mood", mood)),
    )
    manager._broadcast = lambda event, data: calls.append((event, data))
    module, announcer = queue_module
    monkeypatch.setattr(config, "TTS_ENABLED", True)
    monkeypatch.setattr(announcer, "should_announce", lambda context, **kwargs: True)
    monkeypatch.setattr(announcer, "get_announcement", lambda *args, **kwargs: pytest.fail("synchronous LLM"))
    monkeypatch.setattr(announcer, "play_announcement", lambda *args, **kwargs: pytest.fail("synchronous TTS"))
    return files, calls, module, announcer


def test_song_preparation_is_defensive_and_does_not_consume_queue(manager, media):
    files, calls, _, _ = media
    pick = {"song_id": 1, "slot": "exploit"}
    manager._queue = [pick]
    plan = manager.prepare_pick(pick)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert (item.kind, item.filepath, item.song_id, item.duration_ms, item.gain_db) == (
        "song", files["song"], 1, 12500, -3,
    )
    with pytest.raises(FrozenInstanceError):
        item.filepath = "changed"
    with pytest.raises(TypeError):
        plan.activation["changed"] = True
    assert repr(files["song"]) not in repr(plan)
    assert calls == []
    assert manager._queue == [pick]
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT COUNT(*) FROM queue_history").fetchone()[0] == 0


def test_speech_song_chain_keeps_the_listening_session(manager, media):
    files, calls, _, _ = media
    manager._current_session_id = 42
    manager._pregen_cache[1] = {"wav_path": files["tts"], "prev_song_id": 2}
    plan = manager.prepare_pick({"song_id": 1})
    assert manager.execute_plan(plan)
    chain = next(call for call in calls if call[0] == "chain")
    assert chain[2]["session_id"] == 42


def test_cached_tts_remains_available_until_actual_song_start(manager, media):
    files, calls, _, _ = media
    cached = {"wav_path": files["tts"], "prev_song_id": 2}
    manager._pregen_cache[1] = cached
    first = manager.prepare_pick({"song_id": 1})
    second = manager.prepare_pick({"song_id": 1})
    assert [(item.kind, item.role) for item in first.items] == [("speech", "announcement"), ("song", None)]
    assert first.items == second.items
    assert first.items[0].duration_ms == 1000
    assert manager._pregen_cache[1] == cached
    assert calls == []


@pytest.mark.parametrize("restriction", ["stale", "missing", "skip", "disabled", "gate"])
def test_announcement_cache_eligibility_matches_legacy_rules(manager, media, monkeypatch, restriction):
    files, _, _, announcer = media
    cached = {"wav_path": files["tts"], "prev_song_id": 2}
    manager._pregen_cache[1] = cached
    if restriction == "stale":
        cached["prev_song_id"] = 3
    elif restriction == "missing":
        cached["wav_path"] = str(Path(config.DB_PATH).parent / "missing.wav")
    elif restriction == "skip":
        manager._skip_streak = 1
    elif restriction == "disabled":
        database.set_setting("tts_enabled", "false")
    else:
        monkeypatch.setattr(announcer, "should_announce", lambda *args, **kwargs: False)
    assert [item.kind for item in manager.prepare_pick({"song_id": 1}).items] == ["song"]
    assert manager._pregen_cache[1] == cached


@pytest.mark.parametrize("trigger", ["intro", "skip", "mood", "wildcard", "recovery"])
def test_announcement_trigger_priority_is_shared(manager, media, monkeypatch, trigger):
    _, _, _, announcer = media
    decisions = []
    monkeypatch.setattr(announcer, "should_announce", lambda context, **kwargs: decisions.append((context, kwargs)) or True)
    pick = {"song_id": 1}
    if trigger == "skip":
        manager._skip_streak = 1
        manager._prev_mood_state = "chill"
        pick["slot"] = "wildcard"
    elif trigger == "mood":
        manager._prev_mood_state = "chill"
        pick["slot"] = "wildcard"
    elif trigger == "wildcard":
        pick["slot"] = "wildcard"
    elif trigger == "recovery":
        with database.get_connection_cm() as conn:
            conn.execute("UPDATE songs SET recovery_score=0.9 WHERE id=1")
            conn.commit()
    manager.prepare_pick(pick)
    assert decisions[0][0]["trigger"] == trigger
    assert decisions[0][1] == {"advance": False}


def test_guest_effect_file_is_shared_by_preparation_and_desktop(manager, media):
    files, calls, _, _ = media
    manager._dj_effect_cache[1] = files["effect"]
    plan = manager.prepare_pick({"song_id": 1})
    assert plan.items[-1].filepath == files["effect"]
    manager.play_next({"song_id": 1})
    assert calls[0][0] == "song"
    assert calls[0][1][1] == plan.items[-1].filepath
    manager._talk_context.get_active_dj_effect_preset = lambda: None
    assert manager.prepare_pick({"song_id": 1}).items[-1].filepath == files["song"]


def test_processed_mp3_uses_its_own_duration(manager, media, monkeypatch):
    import mutagen
    effect = Path(config.DB_PATH).parent / "nightcore.mp3"
    effect.write_bytes(b"synthetic compressed fixture")
    monkeypatch.setattr(mutagen, "File", lambda path: SimpleNamespace(info=SimpleNamespace(length=9.25)))
    manager._dj_effect_cache[1] = str(effect)
    assert manager.prepare_pick({"song_id": 1}).items[-1].duration_ms == 9250


def test_talk_preparation_filters_processes_and_defers_activation(manager, media, monkeypatch):
    files, calls, module, _ = media
    processed = []
    monkeypatch.setattr(module, "_fade_in_first_second", lambda path: processed.append(("fade", path)) or files["faded"])
    monkeypatch.setattr(module, "_concatenate_wavs", lambda paths: processed.append(("concat", list(paths))) or files["joined"])
    manager._queue = [{"song_id": 1, "artist": "Artist", "title": "Title"}]
    pick = {
        "type": "talk_segment", "wav_paths": [files["tts"], "missing", files["talk"]],
        "segment_type": "caller", "mood_shift": "chill", "persona_id": "caller",
    }
    plan = manager.prepare_pick(pick)
    pick["mood_shift"] = "energetic"
    pick["wav_paths"].clear()
    assert [(item.kind, item.role, item.filepath) for item in plan.items] == [("speech", "talk", files["joined"])]
    assert processed == [("fade", files["tts"]), ("concat", [files["faded"], files["talk"]])]
    assert calls == []
    assert manager._talk_segment_playing is False
    assert manager.execute_plan(plan) is True
    assert calls[0][0] == "chain"
    assert ("mood", "chill") in calls
    assert manager._talk_segment_playing is True
    assert next(call[1] for call in calls if call[0] == "talk_segment_started")["next_song_id"] == 1


def test_failed_physical_start_does_not_activate_talk(manager, media):
    files, calls, _, _ = media
    plan = manager.prepare_pick({"type": "talk_segment", "wav_paths": [files["talk"]], "mood_shift": "chill"})
    manager._player.play_chain = lambda *args, **kwargs: False
    assert manager.execute_plan(plan) is False
    assert calls == []
    assert manager._talk_segment_playing is False


def test_unavailable_pick_can_be_discarded_without_history_or_recommendation_memory(manager, media):
    from engine.playback_plan import MediaUnavailable
    manager._queue = [{"song_id": 99}, {"song_id": 1}]
    reservation = manager.reserve_next()
    with pytest.raises(MediaUnavailable):
        manager.prepare_pick(reservation.item)
    assert manager.discard_reservation(reservation) is True
    assert manager.discard_reservation(reservation) is False
    assert manager._queue == [{"song_id": 1}]
    assert manager._recently_recommended_ids == set()
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT COUNT(*) FROM queue_history").fetchone()[0] == 0


def test_legacy_play_and_talk_use_same_plan_builder(manager, media, monkeypatch):
    files, calls, _, _ = media
    from engine.playback_plan import MediaItem, PreparedPlan
    plan = PreparedPlan(items=(MediaItem("song", files["song"], song_id=1),), song_id=1)
    prepared = []
    monkeypatch.setattr(manager, "prepare_pick", lambda pick: prepared.append(pick) or plan)
    manager.play_next({"song_id": 1})
    manager._play_talk_segment({"type": "talk_segment"})
    assert prepared == [{"song_id": 1}, {"type": "talk_segment"}]
    assert [call[0] for call in calls] == ["song", "song"]


@pytest.fixture
def announcements(queue_module, monkeypatch):
    tts = ModuleType("announcer.tts")
    tts.tts_say = lambda *args, **kwargs: pytest.fail("real TTS")
    personas = ModuleType("announcer.personas")
    personas.get_persona = lambda *args: None
    monkeypatch.setitem(sys.modules, "announcer.tts", tts)
    monkeypatch.setitem(sys.modules, "announcer.personas", personas)
    source = Path(__file__).resolve().parents[1] / "announcer" / "announcements.py"
    spec = importlib.util.spec_from_file_location("announcer._plan_gate_under_test", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_announcement_gate_preview_does_not_advance_frequency(announcements):
    database.set_setting("radio_intro_frequency", "3")
    announcements._radio_intro_count = 2
    announcements._songs_since_last_skip = 4
    context = {"trigger": announcements.AnnounceTrigger.RADIO_INTRO}
    assert announcements.should_announce(context, advance=False) is True
    assert announcements.should_announce(context, advance=False) is True
    assert announcements._radio_intro_count == 2
    assert announcements._songs_since_last_skip == 4
    assert announcements.should_announce(context) is True
    assert announcements._radio_intro_count == 0
    assert announcements._songs_since_last_skip == 5


def test_generated_announcement_is_published_only_on_actual_play(announcements, monkeypatch):
    monkeypatch.setattr(announcements, "llm_chat", lambda: SimpleNamespace(
        chat=lambda **kwargs: SimpleNamespace(content="Prepared line."),
    ))
    assert announcements.get_announcement({}, advance=False) == "Prepared line."
    assert announcements._last_announcement_time == 0
    announcements.mark_announcement_played()
    assert announcements._last_announcement_time > 0


def test_announcement_start_marks_speech_without_counting_an_unheard_song(manager, media, monkeypatch):
    files, calls, _, announcer = media
    manager._pregen_cache[1] = {"wav_path": files["tts"], "prev_song_id": 2}
    stages = []
    monkeypatch.setattr(announcer, "should_announce", lambda *args, advance=True: stages.append(advance) or True)
    monkeypatch.setattr(announcer, "mark_announcement_played", lambda: stages.append("speech"), raising=False)
    plan = manager.prepare_pick({"song_id": 1})
    stages.clear()
    manager.activate_plan(plan, kind="speech")
    assert stages == ["speech"]
    assert 1 not in manager._pregen_cache
    manager.activate_plan(plan, kind="song")
    assert stages == ["speech", True]


def test_coordinator_output_receives_the_whole_private_plan_without_early_activation(manager, media):
    files, calls, _, _ = media
    plan = manager.prepare_pick({"song_id": 1})
    prepared = []
    manager._player = SimpleNamespace(request_prepared_plan=lambda value: prepared.append(value) or True)
    assert manager.execute_plan(plan)
    assert prepared == [plan]
    assert calls == []


def test_corrupt_duration_metadata_does_not_prevent_valid_audio_preparation(manager, media):
    files, calls, _, _ = media
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET duration_secs='unknown' WHERE id=1")
        conn.commit()
    plan = manager.prepare_pick({"song_id": 1})
    assert plan.items[0].duration_ms == 1000
