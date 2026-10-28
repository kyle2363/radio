"""Native playback event regression tests, without initializing VLC or audio."""
import importlib
import logging
from pathlib import Path
import sys
import threading
import queue
from types import SimpleNamespace

import pytest


@pytest.fixture
def playback(monkeypatch):
    monkeypatch.setitem(sys.modules, "vlc", SimpleNamespace())
    spec = importlib.util.spec_from_file_location(
        "_test_player_events", Path(__file__).parents[1] / "engine" / "player.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    logger = logging.getLogger("radio.playback")
    old_handlers, old_level, old_propagate = list(logger.handlers), logger.level, logger.propagate
    spec.loader.exec_module(module)
    pending = []

    class DeferredThread:
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args
            self.name = kwargs.get("name", "")

        def start(self):
            if self.name.startswith("vlc-play"):
                self.target(*self.args)
            else:
                pending.append(lambda: self.target(*self.args))

    monkeypatch.setattr(module, "threading", SimpleNamespace(
        Thread=DeferredThread, Event=threading.Event, Lock=threading.Lock))
    events, callbacks = [], []
    monkeypatch.setattr(module.database, "record_play_event", lambda **event: events.append(event))
    p = module.Player.__new__(module.Player)
    p._lock = threading.Lock()
    p._cb_lock = threading.Lock()
    p._play_gen = 1
    p._completion_gen = 1
    p._completed_items = set()
    p._state = "playing"
    p._current_song_id = 17
    p._current_session_id = 42
    p._current_chain_index = 0
    p._chain_items = [{"song_id": 17, "is_tts": False}]
    p._explicitly_stopped = False
    p._stop_in_progress = False
    p._stop_gen = -1
    p._first_next_gen = -1
    p._talk_playing = False
    p._talk_only_chain = False
    p._tts_events_pending = 0
    p._pre_talk_volume = None
    p._on_song_started_callbacks = []
    p._on_song_ended_callbacks = [callbacks.append]
    p._list_player = SimpleNamespace(stop=lambda: None)
    p._command_lock = threading.RLock()
    p._native_events = queue.SimpleQueue()
    p._transport = None
    p._closed = False
    p._base_volume = 70
    p._gain_db = 0
    p._normalization_enabled = False
    p._current_eq = None
    p._last_eq_preset = None
    p._talk_total_ms = 0
    p._play_started_at = 0
    yield p, events, callbacks, pending
    logger.handlers[:] = old_handlers
    logger.setLevel(old_level)
    logger.propagate = old_propagate


def test_duplicate_completion_records_and_dispatches_once(playback):
    p, events, callbacks, pending = playback
    p._fire_song_ended(17)
    p._fire_song_ended(17)
    for run in pending:
        run()
    assert [event["song_id"] for event in events] == [17]
    assert callbacks == [17]


def test_native_speech_volume_is_session_scoped_and_restores_base(playback, monkeypatch):
    p, *_ = playback
    monkeypatch.setattr(p, '_apply_effective_volume', lambda: None)
    p._talk_playing = True
    p.set_talk_volume(50)
    assert p._base_volume == 35 and p._pre_talk_volume == 70
    p.set_talk_volume(200)
    assert p._base_volume == 100 and p._pre_talk_volume == 70
    p.set_talk_volume(100)
    assert p._base_volume == 70 and p._pre_talk_volume is None


def test_stop_discards_already_deferred_completion_callback(playback):
    p, events, callbacks, pending = playback
    p._on_media_end_reached(None, None)
    p.stop()
    for run in pending:
        run()
    assert len(events) == 1  # Natural end won before the later stop.
    assert callbacks == []  # It must not consume another queue item after stop.


def test_new_play_discards_old_deferred_callback(playback):
    p, events, callbacks, pending = playback
    p._on_media_end_reached(None, None)
    with p._lock:
        p._play_gen += 1
        p._current_song_id = 99
        p._current_session_id = 43
        p._state = "playing"
    for run in pending:
        run()
    assert events[0]["session_id"] == 42
    assert callbacks == []
    assert p._current_song_id == 99


def test_stop_before_native_completion_never_records_natural_end(playback):
    p, events, callbacks, pending = playback
    p.stop()
    p._on_media_end_reached(None, None)
    assert events == []
    assert pending == []


def test_duplicate_native_completion_is_ignored(playback):
    p, events, callbacks, pending = playback
    p._on_media_end_reached(None, None)
    p._on_media_end_reached(None, None)
    for run in pending:
        run()
    assert len(events) == 1
    assert callbacks == [17]


def test_same_song_can_complete_in_a_new_generation(playback):
    p, events, callbacks, pending = playback
    p._fire_song_ended(17)
    pending.pop()()
    p._play_gen += 1
    p._fire_song_ended(17)
    pending.pop()()
    assert len(events) == 2
    assert callbacks == [17, 17]


def test_bound_native_event_from_old_generation_cannot_end_new_song(playback):
    p, events, callbacks, pending = playback
    p._play_gen = 2
    p._current_song_id = 99
    p._on_media_end_reached(None, 1)
    assert p._current_song_id == 99
    assert p._state == "playing"
    assert events == pending == []


def test_stop_between_native_state_update_and_completion_claim(playback, monkeypatch):
    p, events, callbacks, pending = playback
    original = p._fire_song_ended

    def stop_before_claim(song_id, completion):
        p.stop()
        original(song_id, completion)

    monkeypatch.setattr(p, "_fire_song_ended", stop_before_claim)
    p._on_media_end_reached(None, 1)
    assert events == pending == []


def test_completion_uses_captured_session_not_mutable_live_session(playback, monkeypatch):
    p, events, callbacks, pending = playback
    original = p._fire_song_ended

    def change_session_before_claim(song_id, completion):
        p._current_session_id = 999
        original(song_id, completion)

    monkeypatch.setattr(p, "_fire_song_ended", change_session_before_claim)
    p._on_media_end_reached(None, 1)
    assert events[0]["session_id"] == 42


@pytest.mark.parametrize("order", [("_on_end_reached", "_on_media_end_reached"),
                                  ("_on_media_end_reached", "_on_end_reached")])
def test_talk_completion_once_and_no_song_history(playback, order):
    p, events, callbacks, pending = playback
    p._current_song_id = None
    p._talk_only_chain = True
    p._chain_items = [{"is_tts": True}]
    for name in order:
        getattr(p, name)(None, None)
    for run in pending:
        run()
    assert events == []
    assert callbacks == [None]


def test_stopped_talk_never_advances_queue(playback):
    p, events, callbacks, pending = playback
    p._current_song_id = None
    p._talk_only_chain = True
    p._chain_items = [{"is_tts": True}]
    p.stop()
    p._on_end_reached(None, None)
    p._on_media_end_reached(None, None)
    assert pending == events == []


def test_native_stop_callback_does_not_try_to_reacquire_player_lock(playback, monkeypatch):
    p, events, callbacks, pending = playback
    p._current_chain_index = 1
    p._chain_items = [{"is_tts": True}, {"song_id": 17, "is_tts": False}]
    def forbidden_completion(*args):
        pytest.fail("Synchronous native stop callback attempted completion under Player._lock")
    monkeypatch.setattr(p, "_fire_song_ended", forbidden_completion)
    p._list_player.stop = lambda: p._on_next_item(None, None)
    p.stop()
    assert events == pending == []


def test_last_item_next_notification_and_native_end_cannot_double_complete(playback):
    p, events, callbacks, pending = playback
    p._on_next_item(None, 1)
    p._on_media_end_reached(None, 1)
    for run in pending:
        run()
    assert len(events) == 1
    assert callbacks == [17]


def test_pause_resume_after_stop_are_safe_and_cannot_restart_retired_transport(playback):
    p, events, callbacks, pending = playback
    p.stop()
    p.pause()
    p.resume()
    assert p.get_state() == "stopped"


def test_volume_is_remembered_while_stopped(playback):
    p, events, callbacks, pending = playback
    p.stop()
    p.set_volume(25)
    assert p.get_volume() == 25


def test_multiclip_talk_does_not_finish_after_its_first_clip(playback):
    p, events, callbacks, pending = playback
    p._current_song_id = None
    p._talk_only_chain = True
    p._talk_playing = True
    p._tts_events_pending = 2
    p._chain_items = [{"is_tts": True}, {"is_tts": True}]
    p._on_media_end_reached(None, 1)
    assert p.get_state() == "playing"
    assert pending == []
    p._on_next_item(None, 1)
    p._on_media_end_reached(None, 1)
    for run in pending:
        run()
    assert callbacks == [None]
    assert events == []


def test_list_end_cannot_modify_state_outside_the_playback_lock(playback):
    p, events, callbacks, pending = playback
    base_lock = threading.Lock()
    waiting = threading.Event()
    class ObservedLock:
        def __enter__(self):
            waiting.set()
            base_lock.acquire()
        def __exit__(self, *args):
            base_lock.release()
    p._lock = ObservedLock()
    p._current_song_id = None
    p._talk_only_chain = True
    p._chain_items = [{"is_tts": True}]
    base_lock.acquire()
    worker = threading.Thread(target=lambda: p._on_end_reached(None, 1))
    worker.start()
    try:
        assert waiting.wait(1)
        assert p._state == "playing"
    finally:
        base_lock.release()
        worker.join(1)
    assert not worker.is_alive()


@pytest.mark.parametrize("chain", [False, True])
def test_new_native_emitter_is_ready_before_play_and_late_old_event_is_ignored(playback, monkeypatch, chain):
    from unittest.mock import Mock
    p, events, callbacks, pending = playback
    module = sys.modules[type(p).__module__]
    monkeypatch.setattr(module.vlc, "EventType", SimpleNamespace(
        MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2, MediaListPlayerStopped=3), raising=False)
    p._vlc_instance = Mock()
    old_native, old_mp = Mock(), Mock()
    new_native, new_mp = Mock(), Mock()
    p._list_player = old_native
    for native, mp in ((old_native, old_mp), (new_native, new_mp)):
        native.get_media_player.return_value = mp
        native.event_manager.return_value.event_attach.return_value = 0
        mp.event_manager.return_value.event_attach.return_value = 0
    p._vlc_instance.media_list_player_new.side_effect = [old_native, new_native]
    p._vlc_instance.media_player_new.side_effect = [old_mp, new_mp]
    def start_expected():
        # The native emitter must see the right state and have an end handler
        # before it can produce even a very short TTS completion.
        assert p._state == "playing"
        assert p._current_song_id == 17
        assert old_mp.event_manager.return_value.event_attach.called
    old_native.play.side_effect = start_expected
    items = [{"filepath": "synthetic.wav", "song_id": 17, "is_tts": False}]
    assert (p.play_chain(items) if chain else p.play_song(17, "synthetic.wav", 42))
    old_callback = old_mp.event_manager.return_value.event_attach.call_args.args[1]
    assert p.play_song(99, "synthetic-new.wav", 42)
    old_callback(object())
    # Native callbacks only enqueue; dispatching the old origin cannot complete
    # whichever song happens to be current now.
    handler, generation = p._native_events.get_nowait()
    handler(None, generation)
    assert p._current_song_id == 99
    assert events == []
    old_native.stop.assert_called_once()


def test_observed_items_require_native_playing_and_deduplicate_start_end(playback):
    p, events, callbacks, pending = playback
    observed = []
    p._observation_instance = "instance-1"
    p._observation_items = [{"public_item_id": "speech", "is_tts": True, "_media_pointer": 101},
                            {"public_item_id": "song", "song_id": 17, "_media_pointer": 102}]
    p.on_observation(observed.append)
    p._on_native_observation({"kind": "next", "media_pointer": 101}, 1)
    assert observed == []
    for kind, pointer in [("playing", 101), ("playing", 101), ("completed", 101),
                          ("completed", 101), ("next", 102), ("playing", 102),
                          ("paused", 102), ("playing", 102), ("playing", 102),
                          ("completed", 102), ("completed", 102)]:
        p._on_native_observation({"kind": kind, "media_pointer": pointer}, 1)
    assert [(e["item_id"], e["kind"]) for e in observed] == [
        ("speech", "started"), ("speech", "completed"), ("song", "started"),
        ("song", "paused"), ("song", "resumed"), ("song", "completed")]
    assert events == callbacks == pending == []


def test_observation_callback_outside_player_lock_and_stale_origin_rejected(playback):
    p, _, _, _ = playback
    p._observation_instance = "instance-1"
    p._observation_items = [{"public_item_id": "song", "_media_pointer": 101}]
    observed = []
    def callback(event):
        assert p._lock.acquire(blocking=False)
        p._lock.release()
        observed.append(event)
    p.on_observation(callback)
    p._on_native_observation({"kind": "playing", "media_pointer": 101}, 0)
    assert observed == []
    p._on_native_observation({"kind": "playing", "media_pointer": 101}, 1)
    assert len(observed) == 1


def test_observation_unsubscribe_releases_callback_and_is_idempotent(playback):
    p, _, _, _ = playback
    callback = lambda event: None
    unsubscribe = p.on_observation(callback)
    assert p._observation_callbacks == [callback]
    unsubscribe()
    unsubscribe()
    assert p._observation_callbacks == []


def test_observation_unsubscribe_does_not_retain_subscriber(playback):
    import gc
    import weakref
    p, _, _, _ = playback
    class Subscriber:
        def __call__(self, event):
            pass
    callback = Subscriber()
    reference = weakref.ref(callback)
    unsubscribe = p.on_observation(callback)
    del callback
    assert reference() is not None
    unsubscribe()
    gc.collect()
    assert reference() is None


def test_closed_session_output_is_not_retained_by_shared_player(playback):
    import gc
    import weakref
    from engine.vlc_session_output import VlcSessionOutput
    p, _, _, _ = playback
    output = VlcSessionOutput(p, lambda item: "generated.wav", lambda event: None)
    reference = weakref.ref(output)
    assert len(p._observation_callbacks) == 1
    output.close()
    del output
    gc.collect()
    assert reference() is None
    assert p._observation_callbacks == []
