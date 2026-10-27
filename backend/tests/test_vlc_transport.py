from types import SimpleNamespace
from unittest.mock import Mock, call
import threading

import pytest


def transport_parts():
    native = Mock()
    instance = Mock()
    instance.media_list_player_new.return_value = native
    mp, media = Mock(), Mock()
    instance.media_player_new.return_value = mp
    instance.media_list_new.return_value = media
    return instance, native, mp, media


def test_native_callbacks_capture_origin_and_only_enqueue():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    enqueue = Mock()
    handlers = {"next": Mock(), "ended": Mock(), "stopped": Mock(), "media_end": Mock()}
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    transport = VLCTransport(instance, events, 41, handlers, enqueue)
    callback = mp.event_manager.return_value.event_attach.call_args.args[1]
    callback(object())
    enqueue.assert_called_once_with(handlers["media_end"], 41)
    handlers["media_end"].assert_not_called()
    native.set_media_player.assert_called_once_with(mp)
    assert transport.media_list is media


def test_each_generation_has_distinct_native_emitter():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    instance.media_player_new.side_effect = [Mock(), Mock()]
    instance.media_list_player_new.side_effect = [Mock(), Mock()]
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    handlers = dict.fromkeys(("next", "ended", "stopped", "media_end"), Mock())
    first = VLCTransport(instance, events, 1, handlers, Mock())
    second = VLCTransport(instance, events, 2, handlers, Mock())
    assert first.media_player is not second.media_player
    assert first.list_player is not second.list_player


def test_retire_detaches_before_stop_and_releases_only_once():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    owner = Mock()
    owner.attach_mock(native, "native")
    owner.attach_mock(mp.event_manager.return_value, "mp_events")
    transport = VLCTransport(instance, events, 1,
                             dict.fromkeys(("next", "ended", "stopped", "media_end"), Mock()), Mock())
    owner.reset_mock()
    transport.retire()
    transport.retire()
    calls = owner.mock_calls
    assert calls.index(call.mp_events.event_detach(2)) < calls.index(call.native.stop())
    native.stop.assert_called_once()
    native.release.assert_called_once()
    mp.release.assert_called_once()
    media.release.assert_called_once()


def test_retirement_defers_release_until_pending_native_start_finishes():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    entered, unblock = threading.Event(), threading.Event()
    def blocked_play():
        entered.set()
        assert unblock.wait(2)
        return 0
    native.play.side_effect = blocked_play
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    transport = VLCTransport(instance, events, 1,
                             dict.fromkeys(("next", "ended", "stopped", "media_end"), Mock()), Mock())
    worker = threading.Thread(target=transport.play)
    worker.start()
    try:
        assert entered.wait(1)
        transport.retire()
        native.release.assert_not_called()
        mp.release.assert_not_called()
    finally:
        unblock.set()
        worker.join(1)
    assert not worker.is_alive()
    assert native.stop.call_count == 2  # Request stop, then fence a late start.
    native.release.assert_called_once()


def test_media_view_never_dereferences_retired_native_objects():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    transport = VLCTransport(instance, events, 1,
                             dict.fromkeys(("next", "ended", "stopped", "media_end"), Mock()), Mock())
    view = transport.media_view
    transport.retire()
    view.get_time()
    mp.get_time.assert_not_called()


def test_registration_failure_releases_partial_native_resources():
    from engine.vlc_transport import VLCTransport
    instance, native, mp, media = transport_parts()
    native.event_manager.return_value.event_attach.return_value = -1
    events = SimpleNamespace(MediaListPlayerNextItemSet=1, MediaPlayerEndReached=2,
                             MediaListPlayerStopped=3)
    with pytest.raises(RuntimeError, match="registration"):
        VLCTransport(instance, events, 1,
                     dict.fromkeys(("next", "ended", "stopped", "media_end"), Mock()), Mock())
    native.release.assert_called_once()
    mp.release.assert_called_once()
