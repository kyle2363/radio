from unittest.mock import Mock

import pytest


def test_handoff_changes_output_without_replacing_session_port():
    from engine.session_port import SessionPlaybackPort
    first, second = Mock(), Mock()
    first.get_current_song_id.return_value = 1
    second.get_current_song_id.return_value = 2
    port = SessionPlaybackPort()
    old = port.bind_output(first, instance_id="first")
    assert port.get_current_song_id() == 1
    current = port.bind_output(second, instance_id="second")
    assert current > old
    assert port.get_current_song_id() == 2
    assert port.dispatch(old, "stop") is False
    first.stop.assert_not_called()
    second.stop.assert_not_called()
    assert port.dispatch(current, "stop") is True
    second.stop.assert_called_once()


def test_queue_cannot_bypass_coordinator_with_a_physical_start():
    from engine.session_port import SessionPlaybackPort
    port = SessionPlaybackPort()
    with pytest.raises(RuntimeError, match="coordinator"):
        port.play_song(1, "private.wav", 1)
    with pytest.raises(RuntimeError, match="coordinator"):
        port.play_chain([])


def test_empty_port_is_safe_for_recommendations_without_vlc():
    from engine.session_port import SessionPlaybackPort
    port = SessionPlaybackPort()
    assert port.get_current_song_id() is None
    assert port.get_time() == 0
    assert port.get_length() == 0
    assert not port.is_playing()


def test_explicit_prepared_callback_routes_intent_through_coordinator():
    from engine.session_port import SessionPlaybackPort
    request = Mock(return_value=True)
    port = SessionPlaybackPort(request_prepared=request)
    plan = object()
    assert port.request_prepared_plan(plan)
    request.assert_called_once_with(plan)


def test_queue_controls_use_session_authority_instead_of_physical_output():
    from engine.session_port import SessionPlaybackPort
    port, output, stop, seek = SessionPlaybackPort(), Mock(), Mock(), Mock()
    port.bind_output(output)
    port.configure_actions(stop=stop, seek=seek)
    port.stop()
    port.seek(125)
    stop.assert_called_once_with()
    seek.assert_called_once_with(125)
    output.stop.assert_not_called()
    output.seek.assert_not_called()
    with pytest.raises(RuntimeError, match="coordinator"):
        port.pause()
