"""Reservation contracts: preparation is reversible and consumption is explicit."""
from types import SimpleNamespace
import threading

import pytest

import config
from db import database
from tests.test_queue_lifecycle import queue_module, manager


def history():
    with database.get_connection_cm() as conn:
        return [row["song_id"] for row in conn.execute("SELECT song_id FROM queue_history ORDER BY id")]


def test_reserving_does_not_remove_record_or_change_recommendation_memory(manager):
    item = {"song_id": 1, "slot": "exploit"}
    manager._queue = [item]
    reservation = manager.reserve_next()
    assert reservation.item == item
    assert manager._get_display_queue() == [item]
    assert history() == []
    assert manager._recently_recommended_ids == set()
    assert manager.commit_reservation(reservation) == item
    assert manager._queue == []
    assert history() == [1]
    assert manager._recently_recommended_ids == {1}
    assert manager.commit_reservation(reservation) is None
    assert history() == [1]


def test_reservation_defensively_copies_nested_talk_payload(manager):
    item = {"type": "talk_segment", "wav_paths": ["original.wav"]}
    manager._pending_talk_segments = [item]
    reservation = manager.reserve_next()
    reservation.item["wav_paths"].append("changed.wav")
    reservation.item["reason"] = "changed"
    assert manager.commit_reservation(reservation) == item
    assert item == {"type": "talk_segment", "wav_paths": ["original.wav"]}


def test_release_restores_priority_and_order_for_duplicate_song_ids(manager):
    first = {"song_id": 1, "reason": "first"}
    second = {"song_id": 1, "reason": "second"}
    manager._manual_queue = [first, second]
    held = manager.reserve_next()
    other = manager.reserve_next()
    assert other.item == second
    assert manager.release_reservation(held) is True
    assert manager.release_reservation(held) is False
    retry = manager.reserve_next()
    assert retry.item == first
    assert manager.commit_reservation(other) == second
    assert manager._manual_queue == [first]
    assert manager.commit_reservation(retry) == first
    assert history() == [1, 1]


def test_concurrent_reservations_cannot_claim_same_item(manager):
    manager._queue = [{"song_id": song_id} for song_id in (1, 2)]
    start = threading.Barrier(3)
    results = []
    def reserve():
        start.wait(timeout=2)
        results.append(manager.reserve_next())
    workers = [threading.Thread(target=reserve) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.wait(timeout=2)
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert sorted(result.item["song_id"] for result in results) == [1, 2]
    assert manager.reserve_next() is None
    assert history() == []


@pytest.mark.parametrize("invalidation", ["clear", "replacement", "shutdown"])
def test_stale_reservation_cannot_consume_replacement_item(manager, invalidation):
    manager._queue = [{"song_id": 1}]
    reservation = manager.reserve_next()
    if invalidation == "clear":
        manager.clear_queue()
    elif invalidation == "shutdown":
        manager.shutdown(timeout=1)
    # A new equal-valued queue entry is a different item, even across rebuilds.
    manager._queue = [{"song_id": 1}]
    assert manager.commit_reservation(reservation) is None
    assert manager._queue == [{"song_id": 1}]
    assert history() == []


def test_legacy_pop_preserves_priority_and_skips_reserved_items(manager):
    talk = {"type": "talk_segment", "slot": "talk_segment"}
    manual = {"song_id": 1, "slot": "manual"}
    ai = {"song_id": 2, "slot": "exploit"}
    manager._pending_talk_segments = [talk]
    manager._manual_queue = [manual]
    manager._queue = [ai]
    held = manager.reserve_next()
    assert held.item == talk
    assert manager.pop_next() == manual
    assert manager.pop_next() == ai
    assert manager.commit_reservation(held) == talk
    assert history() == [1, 2, None]


def test_empty_reservation_is_nonblocking_and_does_not_request_work(manager, monkeypatch):
    monkeypatch.setattr(manager, "request_rebuild", lambda: pytest.fail("reservation requested inference"))
    monkeypatch.setattr(manager._condition, "wait", lambda **kwargs: pytest.fail("reservation waited"))
    monkeypatch.setattr(manager, "_get_single_recommendation", lambda: pytest.fail("reservation fetched fallback"))
    assert manager.reserve_next() is None


def test_legacy_waiter_does_not_consume_newly_reserved_item(manager, monkeypatch):
    manager._rebuilding = True
    reservations = []
    def complete_rebuild_and_prepare(timeout):
        manager._queue = [{"song_id": 1}]
        reservations.append(manager.reserve_next())
    monkeypatch.setattr(manager._condition, "wait", complete_rebuild_and_prepare)
    monkeypatch.setattr(manager, "_get_single_recommendation", lambda: pytest.fail("reserved fallback"))
    assert manager.pop_next() is None
    assert manager.commit_reservation(reservations[0]) == {"song_id": 1}
    assert history() == [1]


def test_undo_uses_injected_player(manager, monkeypatch):
    calls = []
    manager._player = SimpleNamespace(
        stop=lambda: calls.append("stop"),
        play_song=lambda *args: calls.append(args),
    )
    manager._last_action = {"type": "skip", "song_id": 1, "timestamp": __import__("time").time()}
    manager._last_skipped_song = {"id": 1, "filepath": config.DB_PATH}
    monkeypatch.setattr(database, "undo_play_event", lambda *args: None)
    assert manager.undo_skip() == {"success": True, "song_id": 1}
    assert calls == ["stop", (1, config.DB_PATH, None)]


def test_record_replay_resets_feedback_without_mutating_current_or_manual_queue(manager):
    from unittest.mock import Mock
    manager._queue = [{"song_id": 2, "slot": "exploit"}]
    manager._manual_queue = [{"song_id": 3, "slot": "manual"}]
    manager._pending_talk_segments = [{"type": "talk_segment"}]
    manager._current_song_id = 1
    manager._current_session_id = 44
    manager._skip_streak = 3
    manager._last_action = {"type": "skip", "song_id": 4, "timestamp": 0}
    manager._memory.record_signal = Mock()
    queues = (list(manager._queue), list(manager._manual_queue), list(manager._pending_talk_segments))
    generation = manager._rebuild_gen
    manager.record_replay()
    assert (manager._queue, manager._manual_queue, manager._pending_talk_segments) == queues
    assert manager._rebuild_gen == generation
    assert manager._skip_streak == 0
    assert manager.get_undo_state()["can_undo"] is False
    manager._memory.record_signal.assert_called_once_with(song_id=1, signal="replayed", session_id=44)


def test_energy_and_talk_context_are_injected_per_instance(queue_module, monkeypatch):
    module, _ = queue_module
    calls = []
    trajectory = SimpleNamespace(
        get_direction=lambda: calls.append("direction") or "stable",
        get_target_energy=lambda *args: calls.append("target") or 0.5,
    )
    talk = SimpleNamespace(
        _song_counter=7, _current_guest_takeover=None, _recent_callers=[],
        _recent_guests=[], _recent_segment_types=[],
        get_next_talk_segment=lambda **kwargs: calls.append("talk"),
    )
    instance = module.QueueManager(
        SimpleNamespace(), database, mood_machine=SimpleNamespace(get_state=lambda: "neutral"),
        energy_trajectory=trajectory, talk_context=talk,
    )
    try:
        # Stop the pipeline just after trajectory sampling, before any inference.
        def stop(**kwargs):
            raise RuntimeError("synthetic pipeline boundary")
        monkeypatch.setattr(instance._memory, "get_taste_vectors", stop)
        instance._rebuild_queue(seed_id=1)
        assert calls == ["direction", "target"]
        monkeypatch.setattr(config, "TTS_ENABLED", True)
        database.set_setting("talk_segments_enabled", "true")
        instance._maybe_schedule_talk_segment()
        assert calls == ["direction", "target", "talk"]
        assert talk._song_counter == 8
    finally:
        assert instance.shutdown(timeout=2) == []
