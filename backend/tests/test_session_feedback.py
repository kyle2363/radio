"""Feedback is reversible only in its originating listening history."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

from db import database
from db.playback_store import PlaybackStore
from engine.queue import QueueManager
from engine.session_port import SessionPlaybackPort


def test_undo_exact_event_and_counter_once_across_same_song_histories():
    first, second = database.create_session(), database.create_session()
    database.record_play_event(1, first, 'playing')
    database.record_play_event(1, first, 'skip_early')
    event = database.get_last_play_event(1, 'skip_early', session_id=first)
    database.record_play_event(1, second, 'skip_early')
    assert not database.undo_play_event(1, 'skip_early', session_id=second, event_id=event['id'])
    assert database.undo_play_event(1, 'skip_early', session_id=first, event_id=event['id'])
    assert not database.undo_play_event(1, 'skip_early', session_id=first, event_id=event['id'])
    assert database.get_song(1)['skip_count'] == 1
    assert database.get_last_play_event(1, 'skip_early', session_id=second)['undone'] != 1


def test_signal_flags_are_scoped_and_ordered_even_with_same_timestamp():
    first, second = database.create_session(), database.create_session()
    database.record_play_event(1, first, 'playing')
    database.record_play_event(1, first, 'thumbed_down')
    database.record_play_event(1, second, 'playing')
    assert database.has_signal_since_last_play(1, 'thumbed_down', session_id=first)
    assert not database.has_signal_since_last_play(1, 'thumbed_down', session_id=second)
    event = database.get_last_play_event(1, 'thumbed_down', session_id=first)
    assert database.undo_play_event(1, 'thumbed_down', session_id=first, event_id=event['id'])
    assert database.get_song(1)['skip_count'] == 0


def test_store_skip_threshold_uses_elapsed_seconds_not_duration_percentage():
    store = PlaybackStore()
    for index, (position, duration, signal) in enumerate([(31000, 200000, 'skip_late'), (500, 1000, 'skip_early')]):
        state = store.create_session(f'owner-{index}', 'browser')
        state = store.prepare(state['id'], f'owner-{index}', 'prepare', 0, [{'kind': 'song', 'song_id': index + 1, 'duration_ms': duration}])
        store.observe(state['id'], f'owner-{index}', 'start', state['instance_id'], state['items'][0]['item_id'], 'started')
        store.observe(state['id'], f'owner-{index}', 'skip', state['instance_id'], state['items'][0]['item_id'], 'skipped', position_ms=position)
        assert database.get_last_play_event(index + 1, signal, session_id=state['history_session_id'])


def test_queue_undo_records_origin_and_does_not_change_history_if_selection_fails(monkeypatch, tmp_path):
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'fake')
    history = database.create_session()
    database.record_play_event(1, history, 'skip_late')
    event = database.get_last_play_event(1, 'skip_late')
    queue = QueueManager.__new__(QueueManager)
    queue._db, queue._current_session_id = database, history
    queue._player = SessionPlaybackPort(request_song=lambda *args: False)
    queue.reset_skip_streak = Mock()
    queue.record_action('skip', 1, {'id': 1, 'filepath': str(path)}, 'skip_late')
    assert queue._last_action['event_id'] == event['id']
    assert queue._last_action['session_id'] == history
    assert queue.undo_skip()['success'] is False
    assert database.get_song(1)['skip_count'] == 1
    queue._player = SessionPlaybackPort(request_song=lambda *args: True)
    assert queue.undo_skip()['success']
    assert database.get_song(1)['skip_count'] == 0
    assert not queue.undo_skip()['success']


def test_managed_fallback_waits_for_actual_start(monkeypatch, tmp_path):
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'fake')
    queue = QueueManager.__new__(QueueManager)
    queue._player = SessionPlaybackPort(request_song=lambda *args: True)
    queue._current_session_id = 1
    queue._queue, queue._condition = [{'song_id': 1}], threading.Condition()
    queue._is_reserved = lambda item: False
    queue._db = SimpleNamespace(get_song=lambda sid: {'id': sid, 'filepath': str(path)})
    queue.on_song_started = Mock()
    queue._fallback_play_next_song()
    queue.on_song_started.assert_not_called()


def test_queue_thumbs_undo_retains_original_event_after_history_changes():
    first, second = database.create_session(), database.create_session()
    queue = QueueManager.__new__(QueueManager)
    queue._db, queue._current_session_id = database, first
    database.record_play_event(1, first, 'thumbed_down')
    queue.record_action('thumbs_down', 1)
    database.record_play_event(1, second, 'thumbed_down')
    queue._current_session_id = second
    assert queue.undo_thumbs_down()['success']
    assert not queue.undo_thumbs_down()['success']
    assert database.get_last_play_event(1, 'thumbed_down', session_id=first)['undone'] == 1
    assert database.get_last_play_event(1, 'thumbed_down', session_id=second)['undone'] != 1
    assert database.get_song(1)['skip_count'] == 1


def test_undo_thumb_flag_can_be_reapplied_once_to_same_logical_attempt():
    store = PlaybackStore()
    state = store.create_session('owner', 'browser', output_client_id='page')
    state = store.prepare(state['id'], 'owner', 'prepare', 0, [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000}])
    sid, instance, item = state['id'], state['instance_id'], state['items'][0]['item_id']
    store.observe(sid, 'owner', 'start', instance, item, 'started', client_id='page')
    assert store.observe(sid, 'owner', 'thumb-one', instance, item, 'thumbed_down', client_id='page')['accepted']
    event = database.get_last_play_event(1, 'thumbed_down', session_id=state['history_session_id'])
    assert database.undo_play_event(1, 'thumbed_down', session_id=state['history_session_id'], event_id=event['id'])
    assert store.observe(sid, 'owner', 'thumb-two', instance, item, 'thumbed_down', client_id='page')['accepted']
    assert not store.observe(sid, 'owner', 'thumb-three', instance, item, 'thumbed_down', client_id='page')['accepted']
    # A stale undo retry must leave the re-applied flag and counter intact.
    assert not database.undo_play_event(1, 'thumbed_down', session_id=state['history_session_id'], event_id=event['id'])
    assert database.get_song(1)['skip_count'] == 1
