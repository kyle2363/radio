"""Deterministic worker races, without VLC, model services, or real media."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

import config
from db import database
import engine


@pytest.fixture
def queue_module(monkeypatch):
    # Load the actual queue source with only audio adapters replaced. This avoids
    # importing VLC or initializing a TTS engine during test collection.
    player = ModuleType("engine.player")
    monkeypatch.setitem(sys.modules, "engine.player", player)
    monkeypatch.setattr(engine, "player", player, raising=False)
    announcer = ModuleType("announcer")
    talk = ModuleType("announcer.talk_segments")
    voice = ModuleType("announcer.voice_manager")
    voice._fade_in_first_second = lambda path: path
    voice._concatenate_wavs = lambda paths: paths
    announcer.talk_segments = talk
    announcer.AnnounceTrigger = SimpleNamespace(
        RADIO_INTRO="intro", SKIP_STREAK="skip", MOOD_CHANGE="mood",
        WILDCARD="wildcard", RECOVERY="recovery",
    )
    announcer.should_announce = lambda context: True
    announcer.get_announcement = lambda context, trigger: "Synthetic announcement"
    announcer.play_announcement = lambda *args, **kwargs: "synthetic.wav"
    for name, module in (("announcer", announcer), ("announcer.talk_segments", talk),
                         ("announcer.voice_manager", voice)):
        monkeypatch.setitem(sys.modules, name, module)
    name = "_queue_lifecycle_under_test"
    root = Path(__file__).resolve().parents[2]
    source = Path(os.environ.get("RADIO_QUEUE_TEST_SOURCE", root / "backend" / "engine" / "queue.py"))
    assert source.resolve().is_relative_to(root), "Regression sources must stay inside the checkout"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    yield module, announcer


@pytest.fixture
def manager(queue_module):
    module, _ = queue_module
    instance = module.QueueManager(
        SimpleNamespace(_explicitly_stopped=False), database,
        mood_machine=SimpleNamespace(get_state=lambda: "neutral"),
    )
    yield instance
    assert instance.shutdown(timeout=2.0) == []


def test_replacement_leaves_cancelled_tts_token_set(queue_module, monkeypatch):
    """Also runnable against baseline source: a cancelled event is never reused."""
    module, _ = queue_module
    instance = object.__new__(module.QueueManager)
    instance._tts_checker_stop = threading.Event()
    instance._shutdown = threading.Event()
    instance._worker_lock = threading.RLock()
    instance._start_worker = lambda *args, **kwargs: None
    # Don't execute either monitor: deterministically model an old worker that
    # hasn't been scheduled between cancellation and replacement.
    monkeypatch.setattr(module.threading, "Thread",
                        lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    instance._start_tts_staleness_checker()
    cancelled = instance._tts_checker_stop
    instance._start_tts_staleness_checker()
    assert cancelled.is_set(), "Replacement reactivated the old worker's stop token"


def test_replacing_monitors_permanently_cancels_old_worker(manager):
    manager._start_tts_staleness_checker()
    old_tts = manager._tts_checker_stop
    old_preview = manager._preview_stop
    with manager._worker_lock:
        old_workers = list(manager._workers)
    manager._start_tts_staleness_checker()
    manager._start_preview_watchdog()
    assert old_tts.is_set()
    assert old_preview.is_set()
    assert manager._tts_checker_stop is not old_tts
    assert manager._preview_stop is not old_preview
    assert not manager._tts_checker_stop.is_set()
    for worker in old_workers:
        worker.join(timeout=1)
        assert not worker.is_alive()


def test_shutdown_wakes_empty_queue_waiter_without_fallback(manager, monkeypatch):
    waiting = threading.Event()
    original_wait = manager._condition.wait
    def wait(timeout):
        waiting.set()
        return original_wait(timeout)
    monkeypatch.setattr(manager._condition, "wait", wait)
    monkeypatch.setattr(manager, "_get_single_recommendation",
                        lambda: pytest.fail("Shutdown must not request a recommendation"))
    manager._rebuilding = True
    result = []
    waiter = threading.Thread(target=lambda: result.append(manager.pop_next()))
    waiter.start()
    try:
        assert waiting.wait(1)
        assert manager.shutdown(timeout=1) == []
        waiter.join(1)
        assert not waiter.is_alive()
        assert result == [None]
    finally:
        manager.shutdown(timeout=1)
        waiter.join(1)


def test_shutdown_bounds_join_and_rejects_new_work(manager):
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        release.wait(2)
    worker = manager._start_worker(blocked, name="blocked-test-inference")
    try:
        assert entered.wait(1)
        assert "blocked-test-inference" in manager.shutdown(timeout=0)
        assert manager._start_worker(lambda: pytest.fail("started after shutdown")) is None
        assert manager.request_rebuild() is None
    finally:
        release.set()
        worker.join(1)


@pytest.mark.parametrize("invalidation", ["generation", "cancellation", "shutdown"])
def test_late_tts_result_cannot_overwrite_current_cache(
        manager, queue_module, monkeypatch, invalidation):
    module, announcer = queue_module
    monkeypatch.setattr(config, "TTS_ENABLED", True)
    monkeypatch.setattr(manager._db, "get_song", lambda song_id: {
        "id": song_id, "filepath": str(Path(config.DB_PATH)), "recovery_score": 0,
    })
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def synthesize(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return "stale-speech.wav"
    monkeypatch.setattr(announcer, "play_announcement", synthesize)
    snapshot = module.PlaybackContext(generation=0, current_song_id=1, next_song_id=2)
    worker = manager._start_worker(
        manager._pregen_tts_for_next_song, args=(snapshot,), kwargs={"cancel_event": cancelled},
    )
    try:
        assert entered.wait(1)
        with manager._condition:
            if invalidation == "generation":
                manager._rebuild_gen += 1
            elif invalidation == "cancellation":
                cancelled.set()
            else:
                manager.shutdown(timeout=0)
        manager._pregen_cache[2] = {"wav_path": "current-speech.wav"}
    finally:
        release.set()
        worker.join(1)
    assert not worker.is_alive()
    assert manager._pregen_cache[2] == {"wav_path": "current-speech.wav"}


def test_delayed_rebuild_keeps_its_scheduled_generation(manager, monkeypatch):
    scheduled = []
    monkeypatch.setattr(manager, "_start_worker",
                        lambda target, **options: scheduled.append((target, options)))
    manager.request_rebuild(seed_id=1, advance_talk_counter=True)
    manager.request_rebuild(seed_id=2)
    old_target, old_options = scheduled[0]
    assert old_options["kwargs"]["expected_generation"] == 1
    assert scheduled[1][1]["kwargs"]["expected_generation"] == 2
    monkeypatch.setattr(manager._db, "get_song",
                        lambda song_id: pytest.fail("Stale worker entered recommendation pipeline"))
    old_target(**old_options["kwargs"])
    assert manager._rebuilding is True  # stale work cannot clear the newer rebuild
    assert manager._queue == []


@pytest.fixture
def ranking_pipeline(manager, queue_module, monkeypatch):
    from unittest.mock import Mock
    from engine.session_context import new_talk_context
    from engine.mood import MoodStateMachine
    from engine import archetype
    module, _ = queue_module
    manager._mood = MoodStateMachine(persist_state=False)
    manager._talk_context = new_talk_context()
    manager._talk_context.get_active_guest_slot_bias = lambda: None
    manager._memory.get_taste_vectors = lambda **kw: {'text': [1.0], 'audio': [1.0], 'drift': 0}
    manager._memory.get_repulsion_vector = lambda: None
    manager._memory.get_audio_repulsion_vector = lambda: None
    manager._get_recent_played_context = lambda n: []
    manager._start_worker = Mock()
    manager._start_tts_staleness_checker = Mock()
    talk = Mock(return_value=False)
    manager._maybe_schedule_talk_segment = talk
    monkeypatch.setattr(manager._db, 'get_song', lambda sid: {'id': sid, 'title': str(sid), 'energy': 0.5})
    candidates = [{'song_id': sid, 'score': 1 - sid / 10, 'artist': str(sid), 'energy': 0.5} for sid in range(1, 6)]
    monkeypatch.setattr(module.retriever, 'build_candidate_list', lambda **kw: candidates)
    monkeypatch.setattr(module.slots, 'allocate_slots', lambda *a, **kw: {})
    monkeypatch.setattr(archetype, 'detect_archetype', lambda: ('explorer', {}))
    picks = [{'id': sid, 'slot': 'context', 'reason': 'fixture'} for sid in range(1, 6)]
    llm = Mock(return_value=picks)
    deterministic = Mock(return_value=picks)
    monkeypatch.setattr(module.reranker, 'rerank', llm)
    monkeypatch.setattr(module.reranker, 'deterministic_assignment', deterministic)
    database.set_setting('constellations_enabled', 'false')
    database.set_setting('current_mode', 'radio')
    return llm, deterministic, talk


@pytest.mark.parametrize('enabled', [True, False])
def test_reranking_gate_leaves_dj_scheduling_independent(manager, ranking_pipeline, enabled):
    llm, deterministic, talk = ranking_pipeline
    manager.set_reranking_enabled(enabled)
    manager._rebuild_queue()
    assert llm.call_count == int(enabled)
    assert deterministic.call_count == int(not enabled)
    assert len(manager._queue) == 5
    talk.assert_called_once()


def test_toggle_fences_inflight_ranking_preserving_queue_preview_and_session(manager, ranking_pipeline):
    llm, _, talk = ranking_pipeline
    manager._queue = [{'song_id': 20}]
    manager._manual_queue = [{'song_id': 30}]
    manager._preview_queue = [{'song_id': 40}]
    manager._pending_talk_segments = [{'type': 'talk', 'generation': manager._rebuild_gen}]
    manager._pregen_cache = {20: {'wav_path': 'already-prepared.wav', 'generation': manager._rebuild_gen}}
    original_generation = manager._rebuild_gen
    events = []
    manager._broadcast = lambda event, payload: events.append((event, payload))
    manager._current_session_id, manager._current_song_id = 7, 9
    # Run the toggle inside the LLM call, after configuration was captured.
    def delayed(**kwargs):
        manager.set_reranking_enabled(False)
        return [{'id': sid} for sid in range(1, 6)]
    llm.side_effect = delayed
    manager._rebuild_queue(seed_id=0)
    assert manager._queue == [{'song_id': 20}]
    assert manager._manual_queue == [{'song_id': 30}]
    assert manager._preview_queue == [{'song_id': 40}]
    assert manager._pending_talk_segments == [{'type': 'talk', 'generation': original_generation}]
    assert manager._pregen_cache[20]['wav_path'] == 'already-prepared.wav'
    assert manager._rebuild_gen == original_generation
    assert (manager._current_session_id, manager._current_song_id) == (7, 9)
    assert not manager._rebuilding
    assert events[-1][0] == 'queue_rebuilding' and not events[-1][1]['rebuilding']
    assert events[-1][1]['gen'] > events[0][1]['gen']
    talk.assert_not_called()
    # The next generation honors the changed setting.
    manager._rebuild_queue(seed_id=0)
    assert llm.call_count == 1
    assert manager._preview_queue[0]['song_id'] == 1


def test_toggle_between_admission_and_start_notice_cannot_leave_spinner_running(manager, ranking_pipeline, queue_module, monkeypatch):
    module, _ = queue_module
    events = []
    manager._broadcast = lambda event, payload: events.append((event, payload))
    def before_notice(message):
        if message.startswith('Rebuild queue state:'):
            manager.set_reranking_enabled(False)
    monkeypatch.setattr(module, '_log_step', before_notice)
    manager._rebuild_queue(seed_id=0)
    assert events and all(not payload['rebuilding'] for event, payload in events if event == 'queue_rebuilding')
    assert not manager._rebuilding
    ranking_pipeline[0].assert_not_called()


@pytest.fixture
def fallback_library(manager, queue_module, monkeypatch):
    module, _ = queue_module
    # These files are test-only availability fixtures; they are never decoded.
    root = Path(config.DB_PATH).parent
    with database.get_connection_cm() as conn:
        for song_id in (1, 2):
            path = root / f"available-{song_id}.wav"
            path.write_bytes(b"synthetic audio fixture")
            conn.execute(
                "UPDATE songs SET filepath=?, ingest_phase='clustered', energy=?, "
                "valence=0.5, subfolder='allowed', artist='Allowed Artist', file_mtime=? WHERE id=?",
                (str(path), 0.5 + (song_id - 1) * 0.05, module.time.time() - 86400, song_id),
            )
        conn.commit()
    monkeypatch.setattr(manager._memory, "get_taste_vectors", lambda **kwargs: {"text": [1.0], "audio": None})
    monkeypatch.setattr(module.retriever, "build_candidate_list", lambda **kwargs: [])
    return module


@pytest.mark.parametrize("failure_stage", ["memory", "retriever"])
def test_ai_failure_still_returns_available_database_song(manager, fallback_library, monkeypatch, failure_stage):
    def failed(*args, **kwargs):
        raise TimeoutError("Optional AI service unavailable")
    if failure_stage == "memory":
        monkeypatch.setattr(manager._memory, "get_taste_vectors", failed)
    else:
        monkeypatch.setattr(fallback_library.retriever, "build_candidate_list", failed)
    result = manager._get_single_recommendation()
    assert result is not None
    assert result["song_id"] == 1


@pytest.mark.parametrize("restriction", [
    "current", "blacklist", "artist_blacklist", "cluster_blacklist", "mood_blacklist",
    "suppressed", "recent", "folder", "include_artist", "exclude_artist", "mtime", "missing",
])
def test_database_fallback_preserves_user_restrictions(manager, fallback_library, restriction, monkeypatch):
    filters = {}
    with database.get_connection_cm() as conn:
        if restriction == "current":
            manager._current_song_id = 1
        elif restriction == "blacklist":
            database.blacklist_song(1)
        elif restriction == "artist_blacklist":
            conn.execute("UPDATE songs SET artist='Blocked Artist' WHERE id=1")
            conn.commit()
            database.blacklist_song(1, level="avoid_artist", artist="Blocked Artist")
        elif restriction == "cluster_blacklist":
            conn.execute("UPDATE songs SET cluster_id=7 WHERE id=1")
            conn.commit()
            database.blacklist_song(1, level="avoid_similar", cluster_ids="[7]")
        elif restriction == "mood_blacklist":
            database.blacklist_song(1, level="avoid_mood", mood="chill")
        elif restriction == "suppressed":
            monkeypatch.setattr(database, "get_suppressed_song_ids", lambda: {1})
        elif restriction == "recent":
            conn.execute("INSERT INTO play_events(song_id, signal) VALUES (1, 'completed')")
        elif restriction == "folder":
            conn.execute("UPDATE songs SET subfolder='blocked' WHERE id=1")
            filters = {"folders": ["allowed"]}
        elif restriction in ("include_artist", "exclude_artist"):
            conn.execute("UPDATE songs SET artist='Blocked Artist' WHERE id=1")
            filters = {"artist_mode": restriction.split('_')[0],
                       "artists": ["Allowed" if restriction == "include_artist" else "Blocked"]}
        elif restriction == "mtime":
            conn.execute("UPDATE songs SET file_mtime=? WHERE id=1", (fallback_library.time.time() - 86400 * 10,))
            filters = {"mtime_max_days": 2}
        elif restriction == "missing":
            conn.execute("UPDATE songs SET filepath=? WHERE id=1", (str(Path(config.DB_PATH).parent / "missing.wav"),))
        conn.commit()
    database.set_setting("recommendation_filters", json.dumps(filters))
    result = manager._get_single_recommendation()
    assert result is not None
    assert result["song_id"] == 2


def test_fallback_does_not_ignore_invalid_filter(manager, fallback_library):
    database.set_setting("recommendation_filters", '{"folders": ["allowed"], "mtime_min_days": "broken"}')
    assert manager._get_single_recommendation() is None


def test_missing_candidate_does_not_hide_next_valid_candidate(manager, fallback_library, monkeypatch):
    monkeypatch.setattr(fallback_library.retriever, "build_candidate_list",
                        lambda **kwargs: [{"song_id": 999}, {"song_id": 2}])
    result = manager._get_single_recommendation()
    assert result is not None
    assert result["song_id"] == 2


def test_fallback_normalizes_null_metadata(manager, fallback_library):
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET artist=NULL, title=NULL WHERE id=1")
        conn.commit()
    result = manager._get_single_recommendation()
    assert result["artist"] == ""
    assert result["title"] == ""


def test_manual_addition_wakes_waiter_and_is_consumed_before_ai(manager, monkeypatch):
    waiting = threading.Event()
    original_wait = manager._condition.wait
    def wait(timeout):
        waiting.set()
        return original_wait(timeout)
    monkeypatch.setattr(manager._condition, "wait", wait)
    monkeypatch.setattr(manager, "_get_single_recommendation", lambda: None)
    manager._rebuilding = True
    picks = []
    consumer = threading.Thread(target=lambda: picks.append(manager.pop_next()))
    consumer.start()
    try:
        assert waiting.wait(1)
        manager.add_manual([1])
        consumer.join(1)
        assert not consumer.is_alive()
        assert picks and picks[0] is not None
        assert picks[0]["song_id"] == 1
        assert manager._manual_queue == []
    finally:
        manager.shutdown(timeout=1)
        consumer.join(1)


def test_concurrent_manual_additions_and_consumers_do_not_duplicate(manager):
    gate = threading.Barrier(3)
    def add():
        gate.wait()
        manager.add_manual([1, 2])
    producers = [threading.Thread(target=add) for _ in range(2)]
    for producer in producers:
        producer.start()
    gate.wait()
    for producer in producers:
        producer.join(1)
        assert not producer.is_alive()
    assert [item["song_id"] for item in manager._manual_queue] == [1, 2]
    picks = []
    consumers = [threading.Thread(target=lambda: picks.append(manager.pop_next())) for _ in range(2)]
    for consumer in consumers:
        consumer.start()
    for consumer in consumers:
        consumer.join(1)
        assert not consumer.is_alive()
    assert sorted(pick["song_id"] for pick in picks) == [1, 2]
    assert manager._manual_queue == []
