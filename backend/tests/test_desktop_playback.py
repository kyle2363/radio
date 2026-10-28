from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db.playback_store import ConflictError


def fake_service():
    service = Mock()
    service._vlc_session = None
    service._owners = {}
    service.states, service.contexts = {}, {}
    def create(mode):
        sid = str(len(service.states) + 1)
        service._vlc_session = sid
        service._owners[sid] = 'owner-' + sid
        service.states[sid] = {'id': sid, 'mode': mode, 'revision': 0,
                               'history_session_id': int(sid), 'items': [], 'active_index': 0}
        service.contexts[sid] = SimpleNamespace(port=Mock(), queue=Mock(), mood=Mock())
        return {'session': service.states[sid], 'owner_key': service._owners[sid]}
    service.create.side_effect = create
    service.context_for.side_effect = lambda sid, owner: service.contexts[sid]
    def snapshot(sid, owner):
        if owner != service._owners.get(sid):
            raise PermissionError('Playback owner required')
        return dict(service.states[sid])
    service.store.snapshot.side_effect = snapshot
    service.snapshot.side_effect = service.store.snapshot.side_effect
    def attach(sid, owner):
        state = snapshot(sid, owner)
        if state['mode'] == 'vlc':
            service._vlc_session = sid
        return state
    service.attach.side_effect = attach
    service.coordinator._runtime.return_value.transport_lock = nullcontext()
    return service


def test_desktop_binding_is_lazy_and_follows_exclusive_slot():
    from engine.desktop_playback import DesktopPlayback
    service, factory = fake_service(), Mock()
    factory.return_value = service
    desktop = DesktopPlayback(factory)
    factory.assert_not_called()
    first = desktop.binding()
    assert first.session_id == '1'
    service.states['1']['mode'] = 'browser'
    service._vlc_session = None
    second = desktop.binding()
    assert second.session_id == '2'
    assert second.context.queue is not first.context.queue
    with pytest.raises(ConflictError):
        first.pause()
    service.pause.assert_not_called()


def test_request_scope_never_retargets_a_different_session():
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    desktop = DesktopPlayback(lambda: service)
    with desktop.scope():
        first = desktop.binding()
        service.states['1']['mode'] = 'browser'
        service._vlc_session = None
        assert desktop.binding() is first
        with pytest.raises(ConflictError):
            desktop.binding().stop()
    assert desktop.binding().session_id == '2'


def test_native_output_does_not_initialize_vlc_for_queries():
    from engine.desktop_playback import LazyNativeOutput
    factory = Mock()
    output = LazyNativeOutput(factory)
    assert output.get_state() == 'stopped'
    assert output.get_time() == 0
    output.stop()
    factory.assert_not_called()
    factory.return_value.start.return_value = True
    assert output.start({'instance_id': 'i'})
    factory.assert_called_once_with()
    output.close()
    factory.return_value.close.assert_called_once_with()


def test_explicit_owned_session_scope_controls_browser_without_claiming_vlc():
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    service.create(mode='browser')
    service._vlc_session = None
    desktop = DesktopPlayback(lambda: service)
    with desktop.scope(session_id='1', owner='owner-1'):
        binding = desktop.binding()
        binding.pause()
        assert binding.context is service.contexts['1']
    service.pause.assert_called_once_with('1', 'owner-1')
    assert service.create.call_count == 1


def test_checkpoint_is_lazy_atomic_and_only_saved_when_selected_session_changes(tmp_path, monkeypatch):
    import json
    import os
    from engine import desktop_playback as module
    service = fake_service()
    path = tmp_path / 'private' / 'desktop-controller.json'
    path_factory = Mock(return_value=path)
    replace = Mock(wraps=os.replace)
    monkeypatch.setattr(module.os, 'replace', replace)
    desktop = module.DesktopPlayback(lambda: service, state_path_factory=path_factory)
    path_factory.assert_not_called()
    assert not path.exists()
    first = desktop.binding()
    assert json.loads(path.read_text()) == {'session_id': first.session_id, 'owner_key': first.owner}
    assert replace.call_count == 1
    assert desktop.binding().session_id == first.session_id
    assert desktop.remember(service, first.session_id, first.owner)
    assert replace.call_count == 1
    service.states[first.session_id]['mode'] = 'browser'
    service._vlc_session = None
    assert not desktop.remember(service, first.session_id, first.owner)
    second = desktop.binding()
    assert second.session_id != first.session_id and replace.call_count == 2


def test_valid_checkpoint_reattaches_native_context_without_creating_or_rebuilding(tmp_path):
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    path = tmp_path / 'desktop-controller.json'
    initial = DesktopPlayback(lambda: service, state_path_factory=lambda: path).binding()
    service._vlc_session = None
    restored = DesktopPlayback(lambda: service, state_path_factory=lambda: path).binding()
    assert restored.session_id == initial.session_id and restored.context is initial.context
    assert service.create.call_count == 1
    service.attach.assert_called_once_with(initial.session_id, initial.owner)
    initial.context.queue.request_rebuild.assert_called_once()


@pytest.mark.parametrize('contents', ['invalid-json', '[]', '{}', '{"session_id":"1","owner_key":"bad"}', 'x' * 16385],
                         ids=['malformed', 'array', 'empty', 'bad-owner', 'oversize'])
def test_bad_checkpoint_creates_an_independent_native_session(tmp_path, contents):
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    prior = service.create(mode='browser')
    service._vlc_session = None
    path = tmp_path / 'desktop-controller.json'
    path.write_text(contents)
    binding = DesktopPlayback(lambda: service, state_path_factory=lambda: path).binding()
    assert binding.session_id != prior['session']['id']
    assert service.states[prior['session']['id']]['mode'] == 'browser'
    service.attach.assert_not_called()


def test_symlink_checkpoint_is_never_followed_or_overwritten(tmp_path):
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    target = tmp_path / 'other.json'
    target.write_text('keep me')
    path = tmp_path / 'desktop-controller.json'
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('This Windows account cannot create symlinks')
    desktop = DesktopPlayback(lambda: service, state_path_factory=lambda: path)
    binding = desktop.binding()
    assert not desktop.remember(service, binding.session_id, binding.owner)
    assert path.is_symlink() and target.read_text() == 'keep me'


def test_desktop_durable_interruption_metadata_and_explicit_reactivation():
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    binding = DesktopPlayback(lambda: service).binding()
    service.states[binding.session_id].update(status='interrupted', instance_id='old', position_ms=125,
        items=[{'kind': 'song', 'song_id': 7, 'item_id': 'item'}], active_index=0)
    binding.context.port.get_current_song_id.return_value = None
    binding.context.port.get_state.return_value = 'stopped'
    assert binding.get_state() == 'interrupted'
    assert binding.get_current_song_id() == 7
    service.resume_interrupted.return_value = {'status': 'paused'}
    binding.reactivate()
    service.resume_interrupted.assert_called_once()
    service.resume.assert_called_once_with(binding.session_id, binding.owner)


def test_orderly_restart_reattaches_same_attempt_without_autoplay(tmp_path):
    from db import database
    from db.playback_store import PlaybackStore
    from playback_sessions import PlaybackSessions
    from engine.desktop_playback import DesktopPlayback
    from tests.test_playback_sessions import Queue
    media = Mock()
    media.lookup_song.side_effect = lambda sid: {'filepath': f'/private/{sid}.mp3'}
    native = Mock()
    native.get_time.return_value = 125
    store = PlaybackStore()
    service = PlaybackSessions(store, lambda: media, queue_factory=Queue, vlc_output_factory=lambda *args: native)
    checkpoint = tmp_path / 'desktop-controller.json'
    desktop = DesktopPlayback(lambda: service, state_path_factory=lambda: checkpoint)
    binding = desktop.binding()
    context = binding.context
    context.preferences['auto_advance'] = False
    state = service.next(binding.session_id, binding.owner, 'next', 0)
    item = state['items'][0]
    service.observe(binding.session_id, binding.owner, 'start', state['instance_id'], item['item_id'], 'started', native=True)
    service.set_mood(binding.session_id, binding.owner, 'focus')
    service.set_volume(binding.session_id, binding.owner, 23)
    service.set_vibe(binding.session_id, binding.owner, 1, embedding=[0.25, 0.75])
    service.set_mood(binding.session_id, binding.owner, 'focus')
    service.shutdown()
    persisted = store.snapshot(binding.session_id, binding.owner)
    assert persisted['status'] == 'interrupted' and persisted['items'][0]['attempt_id'] == item['attempt_id']
    assert persisted['position_ms'] == 125
    output = Mock()
    output.get_current_song_id.return_value = None
    output.get_state.return_value = 'stopped'
    restored = PlaybackSessions(store, lambda: media, queue_factory=Queue, vlc_output_factory=lambda *args: output)
    try:
        resumed = DesktopPlayback(lambda: restored, state_path_factory=lambda: checkpoint).binding()
        assert resumed.session_id == binding.session_id and resumed.owner == binding.owner
        assert resumed.get_state() == 'interrupted' and resumed.get_current_song_id() == 1
        assert resumed.get_time() == 125
        snapshot = restored.snapshot(resumed.session_id, resumed.owner)
        assert snapshot['mood'] == 'focus' and snapshot['context']['volume'] == 23
        assert snapshot['vibe_lock']['active']
        output.start.assert_not_called()
        activated = resumed.reactivate()
        assert activated['items'][0]['attempt_id'] == item['attempt_id']
        result = restored.observe(resumed.session_id, resumed.owner, 'restart-start', activated['instance_id'],
            activated['items'][0]['item_id'], 'started', native=True)
        assert result['accepted'] and not result['logical_start']
        assert database.get_song(1)['play_count'] == 1
    finally:
        restored.shutdown()


def test_checkpoint_symlink_guard_without_platform_symlink_permission(tmp_path, monkeypatch):
    from pathlib import Path
    from engine.desktop_playback import DesktopPlayback
    service = fake_service()
    path = tmp_path / 'desktop-controller.json'
    path.write_text('untouched')
    monkeypatch.setattr(Path, 'is_symlink', lambda current: current == path)
    desktop = DesktopPlayback(lambda: service, state_path_factory=lambda: path)
    binding = desktop.binding()
    assert not desktop.remember(service, binding.session_id, binding.owner)
    assert path.read_text() == 'untouched'


def test_checkpoint_tempfile_is_exclusive_and_requests_private_mode(tmp_path, monkeypatch):
    from engine import desktop_playback as module
    original, modes = module.os.open, []
    def open_file(path, flags, *args):
        if str(path).endswith('.tmp'):
            assert flags & module.os.O_EXCL and flags & module.os.O_CREAT
            modes.append(args[0])
        return original(path, flags, *args)
    monkeypatch.setattr(module.os, 'open', open_file)
    service = fake_service()
    module.DesktopPlayback(lambda: service, state_path_factory=lambda: tmp_path / 'desktop.json').binding()
    assert modes == [0o600]
