"""Startup queue fallback behavior without model or audio dependencies."""

import threading

from engine.queue import QueueManager
from tests.test_playback_sessions import domain


def empty_queue(fallback=None):
    queue = object.__new__(QueueManager)
    queue._condition = threading.Condition()
    queue._queue = []
    queue._manual_queue = []
    queue._pending_talk_segments = []
    queue._reservations = {}
    queue._reservation_epoch = 0
    queue._shutdown = threading.Event()
    queue._current_song_id = None
    queue._rebuild_requests = 0
    queue._single_recommendation_filters = lambda: {"subfolder_filter": None}
    queue._get_database_fallback = lambda filters: fallback

    def request_rebuild():
        queue._rebuild_requests += 1

    queue.request_rebuild = request_rebuild
    return queue


def pick(song_id=41, path="synthetic-track.mp3"):
    return {"song_id": song_id, "filepath": path, "title": "Synthetic"}


def test_empty_offline_queue_gets_database_pick_without_consuming_it(tmp_path):
    track = tmp_path / "synthetic-track.mp3"
    track.write_bytes(b"synthetic fixture")
    fallback = pick(path=str(track))
    queue = empty_queue(fallback)

    assert queue.ensure_available(timeout=0) is True
    assert queue._rebuild_requests == 1
    assert queue._queue == [fallback]
    assert queue._reservations == {}


def test_cancelled_request_does_not_insert_fallback():
    queue = empty_queue(pick())
    cancelled = [False]

    def fallback(filters):
        cancelled[0] = True
        return pick()

    queue._get_database_fallback = fallback
    assert queue.ensure_available(timeout=0, cancelled=lambda: cancelled[0]) is False
    assert queue._queue == []


def test_late_rebuild_queue_wins_over_fallback():
    entered_fallback, release_fallback = threading.Event(), threading.Event()
    queue = empty_queue()

    def fallback(_filters):
        entered_fallback.set()
        assert release_fallback.wait(2)
        return pick(99)

    queue._get_database_fallback = fallback
    result = []
    worker = threading.Thread(target=lambda: result.append(queue.ensure_available(timeout=0)))
    worker.start()
    assert entered_fallback.wait(2)
    with queue._condition:
        queue._queue.append(pick(7))
        queue._condition.notify_all()
    release_fallback.set()
    worker.join(2)

    assert not worker.is_alive()
    assert result == [True]
    assert [item["song_id"] for item in queue._queue] == [7]


def test_playback_next_asks_empty_session_queue_to_ensure_availability(domain):
    service, sid, owner, *_ = domain
    queue = service.context_for(sid, owner).queue
    queue.items.clear()
    calls = []

    def ensure_available(*, timeout, cancelled):
        calls.append(timeout)
        if not cancelled():
            queue.items.append({"song_id": 1, "title": "One", "filepath": "PRIVATE"})
        return True

    queue.ensure_available = ensure_available
    plan = service.next(sid, owner, "first-play", 0)

    assert calls == [1.0]
    assert plan["items"][0]["song_id"] == 1
