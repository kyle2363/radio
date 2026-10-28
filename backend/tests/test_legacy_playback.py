import importlib
from unittest.mock import Mock


def controller(monkeypatch):
    main = importlib.import_module('main')
    player, queue = Mock(), Mock()
    monkeypatch.setattr(main.player_engine, 'get_player', lambda: player)
    monkeypatch.setattr(main, '_get_queue_manager', lambda: queue)
    monkeypatch.setattr(main.database, 'record_play_event', Mock())
    return main, player, queue


def test_legacy_select_waits_for_observation_before_history(monkeypatch, tmp_path):
    main, player, queue = controller(monkeypatch)
    track = tmp_path / 'track.wav'
    track.write_bytes(b'synthetic')
    monkeypatch.setattr(main.database, 'get_song', lambda sid: {'id': sid, 'filepath': str(track)})
    monkeypatch.setattr(main, '_ensure_session', lambda: 7)
    assert main.play_song(1)['song_id'] == 1
    player.play_song.assert_called_once()
    main.database.record_play_event.assert_not_called()
    queue.on_song_started.assert_not_called()


def test_legacy_idle_resume_uses_non_consuming_coordinator_advance(monkeypatch):
    main, player, queue = controller(monkeypatch)
    player.get_state.return_value = 'stopped'
    player.get_current_song_id.return_value = None
    player.next.return_value = {'items': []}
    main.resume_playback()
    player.next.assert_called_once_with()
    queue.pop_next.assert_not_called()
    queue.on_song_started.assert_not_called()


def test_recommendation_fetch_does_not_consume_queue(monkeypatch):
    main, player, queue = controller(monkeypatch)
    queue._get_display_queue.return_value = [{'song_id': 3, 'slot': 'explore', 'reason': 'test'}]
    assert main.get_recommendation()['song_id'] == 3
    queue.pop_next.assert_not_called()
