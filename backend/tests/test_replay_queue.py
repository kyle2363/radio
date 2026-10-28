"""Explicit replay preserves the active plan and queue reservation."""
from copy import deepcopy

from db import database
from tests.test_playback_sessions import domain, start


def test_replay_real_queue_feedback_records_one_durable_signal(domain):
    from engine.queue import QueueManager
    from engine.taste import MemorySystem
    from unittest.mock import Mock

    service, sid, owner, *_ = domain
    _, started = start(service, sid, owner)
    queue = service.context_for(sid, owner).queue
    queue._current_song_id = 1
    queue._current_session_id = started['session']['history_session_id']
    queue._memory = MemorySystem()
    queue.reset_skip_streak = Mock()
    queue.clear_undo = Mock()
    queue.record_replay = QueueManager.record_replay.__get__(queue)
    service.replay(sid, owner, 'actual-replay', started['session']['revision'])
    with database.get_connection_cm() as conn:
        count = conn.execute("SELECT COUNT(*) FROM play_events WHERE signal='replayed'").fetchone()[0]
    assert count == 1


def test_replay_restarts_current_item_idempotently_without_queue_rebuild(domain):
    service, sid, owner, _media, events, _native = domain
    plan, started = start(service, sid, owner)
    item = plan["items"][0]
    queue = service.context_for(sid, owner).queue
    queue_before = deepcopy(queue.items)
    service.progress(sid, owner, plan["instance_id"], item["item_id"], 1, 7300, client_id="tab-a")
    paused = service.observe(sid, owner, "pause-observation", plan["instance_id"], item["item_id"],
                             "paused", position_ms=7300, client_id="tab-a")
    revision = paused["session"]["revision"]

    replayed = service.replay(sid, owner, "replay-once", revision)
    assert replayed["instance_id"] == plan["instance_id"]
    assert replayed["items"][replayed["active_index"]]["item_id"] == item["item_id"]
    assert replayed["status"] == "paused"
    assert replayed["position_ms"] == 0
    assert replayed["revision"] == revision + 1
    assert queue.items == queue_before
    assert queue.starts == [(1, replayed["history_session_id"])]
    assert queue.request_rebuild.call_count == 0
    queue.record_replay.assert_called_once_with(persist=False)
    assert database.get_song(1)["play_count"] == 1
    assert database.get_song(1)["replay_count"] == 1

    retried = service.replay(sid, owner, "replay-once", revision)
    assert retried["duplicate"] is True
    assert retried["revision"] == replayed["revision"]
    assert queue.record_replay.call_count == 1
    assert database.get_song(1)["replay_count"] == 1

    second = service.replay(sid, owner, "replay-twice", replayed["revision"])
    assert second["position_ms"] == 0
    assert database.get_song(1)["play_count"] == 1
    assert database.get_song(1)["replay_count"] == 2
    seek_controls = [data for event, data in events if event == "playback_control" and data.get("action") == "seek"]
    assert [entry["position_ms"] for entry in seek_controls] == [0, 0]
    assert queue.items == queue_before
    assert queue.request_rebuild.call_count == 0
