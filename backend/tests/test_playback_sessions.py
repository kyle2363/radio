"""Domain session contract: no device, model or live library required."""
from types import SimpleNamespace
from unittest.mock import Mock
import json
import threading

import pytest

from db.playback_store import PlaybackStore, ConflictError
from engine.playback_plan import MediaItem, PreparedPlan


class Queue:
    def __init__(self, player, db_handler, **kwargs):
        self._player, self._db = player, db_handler
        self._mood = kwargs['mood_machine']
        self._energy_trajectory = kwargs['energy_trajectory']
        self._talk_context = kwargs['talk_context']
        self._announcements = kwargs['announcement_context']
        self.items = [{'song_id': 1, 'title': 'One', 'filepath': 'PRIVATE'}, {'song_id': 2}]
        self.claims, self.starts, self.activations = [], [], []
        self.record_completion = Mock()
        self.record_heart = Mock()
        self.on_song_ended = Mock()
        self.request_rebuild = Mock()
        self.shutdown = Mock()
    def reserve_next(self):
        available = [i for i in self.items if all(c.item is not i for c in self.claims)]
        if not available:
            return None
        claim = SimpleNamespace(item=available[0])
        self.claims.append(claim)
        return claim
    def commit_reservation(self, claim):
        self.claims.remove(claim)
        self.items.remove(claim.item)
        return claim.item
    def release_reservation(self, claim):
        if claim in self.claims:
            self.claims.remove(claim)
    def prepare_pick(self, pick):
        return PreparedPlan((MediaItem('song', f'/private/{pick["song_id"]}.mp3', pick['song_id'], 1000),))
    def activate_plan(self, plan, **kwargs):
        self.activations.append((plan, kwargs))
    def on_song_started(self, song_id, session_id=None):
        self.starts.append((song_id, session_id))
    def _get_display_queue(self):
        return self.items
    def add_manual(self, song_ids, position='back'):
        self.items.extend({'song_id': i} for i in song_ids)
        return {'added': len(song_ids)}
    def clear_queue(self):
        self.items.clear()
    def remove_manual(self, song_id):
        self.items[:] = [i for i in self.items if i['song_id'] != song_id]
        return True
    def reorder_display_queue(self, order):
        self.items.sort(key=lambda i: order.index(i['song_id']))
        return self.items


@pytest.fixture
def domain():
    from playback_sessions import PlaybackSessions
    media = Mock()
    media.lookup_song.side_effect = lambda sid: {'filepath': f'/private/{sid}.mp3'}
    media.register_generated.return_value = 'r' * 64
    events = []
    native = Mock()
    native.quiesce.side_effect = lambda instance_id, command_id: {
        'confirmed': True, 'instance_id': instance_id, 'command_id': command_id, 'position_ms': 175}
    service = PlaybackSessions(PlaybackStore(), lambda: media, queue_factory=Queue,
                               emit=lambda event, data: events.append((event, data)),
                               vlc_output_factory=lambda *args: native)
    created = service.create(client_id='tab-a')
    sid, owner = created['session']['id'], created['owner_key']
    service.context_for(sid, owner).preferences['auto_advance'] = False
    yield service, sid, owner, media, events, native
    service.shutdown()


def start(service, sid, owner):
    plan = service.next(sid, owner, 'next', 0)
    item = plan['items'][0]
    result = service.observe(sid, owner, 'start', plan['instance_id'], item['item_id'], 'started', client_id='tab-a')
    return plan, result


def test_constructor_is_lazy_and_owner_is_only_returned_once(domain):
    service, sid, owner, media, events, native = domain
    assert len(owner) >= 32
    assert service.snapshot(sid, owner)['output_client_id'] == 'tab-a'
    assert owner not in json.dumps(events)
    assert owner not in json.dumps(service.snapshot(sid, owner))
    assert 'PRIVATE' not in json.dumps(service.snapshot(sid, owner))
    with pytest.raises(PermissionError):
        service.snapshot(sid, 'wrong')


def test_preload_reserves_but_only_actual_start_updates_queue(domain):
    service, sid, owner, *_ = domain
    q = service.context_for(sid, owner).queue
    plan = service.next(sid, owner, 'next', 0)
    assert len(q.items) == 2 and q.starts == [] and q.activations == []
    assert '/private' not in json.dumps(plan)
    assert plan['items'][0]['url'] == '/api/media/tracks/1/stream'
    item = plan['items'][0]
    wrong = service.observe(sid, owner, 'bad', plan['instance_id'], item['item_id'], 'started', client_id='tab-other')
    assert not wrong['accepted'] and len(q.items) == 2
    accepted = service.observe(sid, owner, 'start', plan['instance_id'], item['item_id'], 'started', client_id='tab-a')
    assert accepted['logical_start'] and len(q.items) == 1 and len(q.starts) == 1
    service.observe(sid, owner, 'start', plan['instance_id'], item['item_id'], 'started', client_id='tab-a')
    assert len(q.starts) == 1


def test_independent_contexts_and_controls(domain):
    service, sid, owner, *_ = domain
    other = service.create(client_id='tab-b')
    oid, key = other['session']['id'], other['owner_key']
    left, right = service.context_for(sid, owner), service.context_for(oid, key)
    assert left.queue is not right.queue and left.mood is not right.mood
    assert left.talk is not right.talk and left.energy is not right.energy
    service.set_mood(sid, owner, 'chill')
    service.set_volume(sid, owner, 23)
    assert service.snapshot(sid, owner)['mood'] == 'chill'
    assert service.snapshot(oid, key)['mood'] == 'neutral'
    assert service.snapshot(oid, key)['preferences']['volume'] != 23


def test_handoff_preserves_context_attempt_and_requires_correlated_ack(domain):
    service, sid, owner, media, events, native = domain
    plan, _ = start(service, sid, owner)
    context = service.context_for(sid, owner)
    revision = service.snapshot(sid, owner)['revision']
    pending = service.handoff(sid, owner, 'move', revision, 'vlc')
    assert pending['awaiting_confirmation']
    with pytest.raises(ValueError):
        service.ack_quiesce(sid, owner, 'wrong', 'move', 175, client_id='tab-a')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 175, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', revision, 'vlc')
    assert moved['id'] == sid and moved['mode'] == 'vlc' and moved['position_ms'] == 175
    assert moved['items'][0]['attempt_id'] == plan['items'][0]['attempt_id']
    assert service.context_for(sid, owner) is context and context.output is native
    assert context.queue.starts == [(1, moved['history_session_id'])]
    denied = service.observe(sid, owner, 'late', plan['instance_id'], plan['items'][0]['item_id'], 'completed', client_id='tab-a')
    assert not denied['accepted']


def test_occupied_vlc_rejected_before_browser_is_paused(domain):
    service, sid, owner, media, events, native = domain
    service.create(mode='vlc')
    plan, _ = start(service, sid, owner)
    before = len(events)
    with pytest.raises(ConflictError):
        service.handoff(sid, owner, 'move', service.snapshot(sid, owner)['revision'], 'vlc')
    assert not any(event == 'playback_control' for event, data in events[before:])


def test_generated_plan_leases_released_on_stop_and_no_private_paths(domain):
    service, sid, owner, media, *_ = domain
    q = service.context_for(sid, owner).queue
    q.prepare_pick = lambda pick: PreparedPlan((MediaItem('speech', '/private/speech.wav', duration_ms=100),
                                               MediaItem('song', '/private/effect.wav', 1, 1000)))
    plan = service.next(sid, owner, 'next', 0)
    assert media.register_generated.call_count == 2
    assert '/private' not in json.dumps(plan)
    lease = media.register_generated.call_args_list[0].args[1]
    service.stop(sid, owner, 'stop', plan['revision'])
    media.release_session.assert_called_with(lease)


def test_progress_is_monotonic_and_checkpoint_does_not_advance_revision(domain):
    service, sid, owner, *_ = domain
    plan, started = start(service, sid, owner)
    item = plan['items'][0]
    revision = started['session']['revision']
    first = service.progress(sid, owner, plan['instance_id'], item['item_id'], 1, 50, client_id='tab-a')
    assert first['accepted']
    second = service.progress(sid, owner, plan['instance_id'], item['item_id'], 2, 80, client_id='tab-a')
    assert second['accepted']
    stale = service.progress(sid, owner, plan['instance_id'], item['item_id'], 1, 20, client_id='tab-a')
    assert not stale['accepted']
    state = service.snapshot(sid, owner)
    assert state['position_ms'] == 80 and state['revision'] == revision


def test_completion_feedback_once_and_context_persists_without_paths(domain):
    service, sid, owner, *_ = domain
    plan, result = start(service, sid, owner)
    item = plan['items'][0]
    service.observe(sid, owner, 'end', plan['instance_id'], item['item_id'], 'completed', position_ms=1000, client_id='tab-a')
    service.observe(sid, owner, 'end', plan['instance_id'], item['item_id'], 'completed', position_ms=1000, client_id='tab-a')
    q = service.context_for(sid, owner).queue
    q.record_completion.assert_called_once()
    service.set_mood(sid, owner, 'focus')
    persisted = service.store.snapshot(sid, owner)
    assert persisted['context']['mood'] == 'focus' and 'PRIVATE' not in json.dumps(persisted['context'])


def test_shutdown_refuses_new_admission(domain):
    service, sid, owner, *_ = domain
    service.shutdown()
    with pytest.raises(ConflictError):
        service.create()


def test_constructor_does_not_open_storage_or_instantiate_runtime():
    from playback_sessions import PlaybackSessions
    store, media_factory, queue_factory = Mock(), Mock(), Mock()
    service = PlaybackSessions(store, media_factory, queue_factory=queue_factory)
    assert not store.mock_calls and not media_factory.mock_calls and not queue_factory.mock_calls
    service.shutdown()


def test_actual_speech_and_song_activate_separately_and_handoff_not_twice(domain):
    service, sid, owner, media, events, native = domain
    q = service.context_for(sid, owner).queue
    q.prepare_pick = lambda pick: PreparedPlan((MediaItem('speech', '/private/speech.wav', duration_ms=100),
                                               MediaItem('song', '/private/1.mp3', 1, 1000)))
    plan = service.next(sid, owner, 'next', 0)
    speech, song = plan['items']
    service.observe(sid, owner, 'speech-start', plan['instance_id'], speech['item_id'], 'started', client_id='tab-a')
    assert q.starts == [] and len(q.items) == 2
    service.observe(sid, owner, 'speech-end', plan['instance_id'], speech['item_id'], 'completed', position_ms=100, client_id='tab-a')
    service.observe(sid, owner, 'song-start', plan['instance_id'], song['item_id'], 'started', client_id='tab-a')
    assert len(q.starts) == 1 and len(q.items) == 1
    revision = service.snapshot(sid, owner)['revision']
    service.handoff(sid, owner, 'move', revision, 'vlc')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 200, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', revision, 'vlc')
    service.observe(sid, owner, 'native-start', moved['instance_id'], moved['items'][0]['item_id'], 'started', native=True)
    assert len(q.starts) == 1 and len(q.activations) == 2


def test_late_cancelled_preparation_only_releases_its_own_lease(domain):
    service, sid, owner, media, *_ = domain
    q = service.context_for(sid, owner).queue
    entered, release = threading.Event(), threading.Event()
    def prepare(pick):
        entered.set()
        assert release.wait(3)
        return PreparedPlan((MediaItem('song', '/private/effect.wav', 1, 1000),))
    q.prepare_pick = prepare
    result = []
    worker = threading.Thread(target=lambda: result.append(service.next(sid, owner, 'slow', 0)))
    worker.start()
    assert entered.wait(2)
    service.stop(sid, owner, 'stop', 0)
    release.set()
    worker.join(3)
    assert not worker.is_alive() and result[0]['cancelled']
    lease = media.register_generated.call_args.args[1]
    media.release_session.assert_called_with(lease)
    assert not service._prepared and len(q.items) == 2


def test_checkpoint_throttles_storage_while_position_remains_current(domain, monkeypatch):
    service, sid, owner, *_ = domain
    plan, _ = start(service, sid, owner)
    item = plan['items'][0]
    checkpoint = Mock(wraps=service.store.checkpoint_position)
    monkeypatch.setattr(service.store, 'checkpoint_position', checkpoint)
    now = [0]
    service._clock = lambda: now[0]
    for seq in range(1, 5):
        now[0] = seq
        service.progress(sid, owner, plan['instance_id'], item['item_id'], seq, seq * 10, client_id='tab-a')
    assert checkpoint.call_count == 1
    assert service.store.snapshot(sid, owner)['position_ms'] == 10
    assert service.snapshot(sid, owner)['position_ms'] == 40
    now[0] = 6
    service.progress(sid, owner, plan['instance_id'], item['item_id'], 5, 50, client_id='tab-a')
    assert checkpoint.call_count == 2


def test_attach_recovers_bounded_context_without_start_and_resume_keeps_attempt(domain):
    from playback_sessions import PlaybackSessions
    service, sid, owner, media, events, native = domain
    plan, _ = start(service, sid, owner)
    service.set_mood(sid, owner, 'focus')
    service.context_for(sid, owner).talk._song_counter = 7
    service.set_volume(sid, owner, 32)
    service.store.recover_interrupted()
    restored_events = []
    recovered = PlaybackSessions(service.store, lambda: media, queue_factory=Queue,
                                 emit=lambda *args: restored_events.append(args))
    restored = recovered.attach(sid, owner)
    assert restored['status'] == 'interrupted' and restored['mood'] == 'focus'
    assert restored['preferences']['volume'] == 32 and restored_events == []
    context = recovered.context_for(sid, owner)
    context.preferences['auto_advance'] = False
    assert context.talk._song_counter == 7
    resumed = recovered.resume_interrupted(sid, owner, 'resume', restored['revision'], 'new-tab')
    assert resumed['items'][0]['attempt_id'] == plan['items'][0]['attempt_id']
    result = recovered.observe(sid, owner, 'resumed', resumed['instance_id'], resumed['items'][0]['item_id'],
                               'started', client_id='new-tab')
    assert result['accepted'] and not result['logical_start'] and context.queue.starts == []
    recovered.shutdown()


def test_close_releases_admission_and_does_not_stop_other_session(domain):
    service, sid, owner, *_ = domain
    other = service.create(client_id='tab-other')
    other_id, other_owner = other['session']['id'], other['owner_key']
    service.close(sid, owner)
    assert service.snapshot(other_id, other_owner)['status'] == 'idle'
    with pytest.raises(ConflictError):
        service.context_for(sid, owner)


def test_sleep_timer_follows_same_session_through_handoff_and_only_stops_it(domain):
    service, sid, owner, media, events, native = domain
    other = service.create(client_id='other')
    timers = []
    class Timer:
        def __init__(self, delay, callback, args):
            self.delay, self.callback, self.args = delay, callback, args
            self.cancelled = False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    now = [1000]
    service._wall_clock = lambda: now[0]
    service._timer_factory = Timer
    plan, _ = start(service, sid, owner)
    timer = service.set_sleep_timer(sid, owner, 1, fade_seconds=0)
    assert timer['expires_at'] == 1060 and timers[0].delay == 60
    revision = service.snapshot(sid, owner)['revision']
    service.handoff(sid, owner, 'move', revision, 'vlc')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 20, client_id='tab-a')
    service.handoff(sid, owner, 'move', revision, 'vlc')
    now[0] = 1061
    timers[0].callback(*timers[0].args)
    assert service.snapshot(sid, owner)['status'] == 'stopped'
    assert service.get_sleep_timer(sid, owner) is None
    assert service.snapshot(other['session']['id'], other['owner_key'])['status'] == 'idle'


def test_stop_cancels_unconfirmed_vlc_reservation(domain):
    service, sid, owner, *_ = domain
    plan, _ = start(service, sid, owner)
    revision = service.snapshot(sid, owner)['revision']
    assert service.handoff(sid, owner, 'move', revision, 'vlc')['awaiting_confirmation']
    service.stop(sid, owner, 'stop', revision)
    admitted = service.create(mode='vlc')
    assert admitted['session']['mode'] == 'vlc'


def test_native_snapshot_checkpoints_actual_position_without_command_revision(domain):
    service, sid, owner, media, events, native = domain
    plan, _ = start(service, sid, owner)
    revision = service.snapshot(sid, owner)['revision']
    service.handoff(sid, owner, 'move', revision, 'vlc')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 20, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', revision, 'vlc')
    service.observe(sid, owner, 'native-start', moved['instance_id'], moved['items'][0]['item_id'], 'started', native=True)
    native.get_time.return_value = 450
    revision = service.store.snapshot(sid, owner)['revision']
    state = service.snapshot(sid, owner)
    assert state['position_ms'] == 450
    assert service.store.snapshot(sid, owner)['position_ms'] == 450
    assert service.store.snapshot(sid, owner)['revision'] == revision


def test_generated_lease_is_renewed_on_active_snapshot_and_throttled(domain):
    service, sid, owner, media, events, native = domain
    q = service.context_for(sid, owner).queue
    q.prepare_pick = lambda pick: PreparedPlan((MediaItem('song', '/private/effect.wav', 1, 1000),))
    now = [0]
    service._clock = lambda: now[0]
    plan, _ = start(service, sid, owner)
    lease = media.register_generated.call_args.args[1]
    service.snapshot(sid, owner)
    media.renew_session.assert_called_with(lease)
    count = media.renew_session.call_count
    now[0] = 1
    service.snapshot(sid, owner)
    assert media.renew_session.call_count == count
    now[0] = 301
    service.snapshot(sid, owner)
    assert media.renew_session.call_count == count + 1


def test_session_port_legacy_requests_use_coordinator_and_ignore_client_paths(domain):
    service, sid, owner, *_ = domain
    context = service.context_for(sid, owner)
    plan = context.port.play_song(2, '/untrusted/request.mp3', 999)
    assert plan['items'][0]['song_id'] == 2
    assert plan['items'][0]['url'] == '/api/media/tracks/2/stream'
    assert '/untrusted' not in json.dumps(plan)
    assert len(context.queue.items) == 2 and context.queue.starts == []


def test_controller_can_own_skip_and_next_without_server_auto_advance(domain):
    service, sid, owner, *_ = domain
    context = service.context_for(sid, owner)
    context.preferences['auto_advance'] = True
    plan, _ = start(service, sid, owner)
    result = service.observe(sid, owner, 'skip', plan['instance_id'], plan['items'][0]['item_id'],
                              'skipped', position_ms=50, client_id='tab-a', auto_advance=False)
    assert result['accepted'] and result['session']['status'] == 'idle'
    assert service._jobs == {}
    next_plan = service.next(sid, owner, 'next-after-skip', result['session']['revision'])
    assert next_plan['items'][0]['song_id'] == 2


def test_session_port_stop_uses_durable_coordinator_and_closes_old_grant(domain):
    service, sid, owner, *_ = domain
    plan, _ = start(service, sid, owner)
    context = service.context_for(sid, owner)
    stopped = context.port.stop()
    assert stopped['status'] == 'stopped' and stopped['instance_id'] is None
    late = service.observe(sid, owner, 'late', plan['instance_id'], plan['items'][0]['item_id'],
                            'completed', client_id='tab-a')
    assert not late['accepted']


def test_output_factory_failure_does_not_leave_a_half_attached_runtime(domain):
    service, sid, owner, *_ = domain
    service._vlc_output_factory = Mock(side_effect=RuntimeError('device unavailable'))
    with pytest.raises(RuntimeError, match='device unavailable'):
        service.create(mode='vlc')
    assert set(service.coordinator._runtimes) == {sid}
    assert service._vlc_session is None


@pytest.mark.parametrize('failure', ['stale_revision', 'factory'])
def test_failed_handoff_does_not_reserve_free_desktop_output(domain, failure):
    service, sid, owner, *_ = domain
    factory = service._vlc_output_factory
    if failure == 'factory':
        service._vlc_output_factory = Mock(side_effect=RuntimeError('native construction failed'))
        expected, revision = RuntimeError, 0
    else:
        expected, revision = ConflictError, 999
    with pytest.raises(expected):
        service.handoff(sid, owner, 'bad-move', revision, 'vlc')
    assert service._vlc_session is None
    assert service.snapshot(sid, owner)['mode'] == 'browser'
    service._vlc_output_factory = factory
    assert service.create(mode='vlc')['session']['mode'] == 'vlc'


@pytest.mark.parametrize('method,output_method,args', [
    ('pause', 'pause', ()), ('resume', 'resume', ()), ('seek', 'seek', (20,)),
    ('set_volume', 'set_volume', (23,)), ('set_eq', 'set_eq_preset', ('flat',)),
    ('set_normalization', 'set_normalization_enabled', (False,)),
])
def test_handoff_waits_for_already_admitted_output_control(domain, method, output_method, args):
    service, sid, owner, media, events, native = domain
    moved = service.handoff(sid, owner, 'native', 0, 'vlc')
    entered, release, moved_back = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def block(*unused):
        entered.set()
        assert release.wait(3)
        return True
    getattr(native, output_method).side_effect = block
    def control():
        try:
            getattr(service, method)(sid, owner, *args)
        except Exception as exc:
            errors.append(exc)
    def transfer():
        try:
            service.handoff(sid, owner, 'browser', moved['revision'], 'browser', 'new-tab')
        except Exception as exc:
            errors.append(exc)
        finally:
            moved_back.set()
    controlling = threading.Thread(target=control)
    transferring = threading.Thread(target=transfer)
    controlling.start()
    assert entered.wait(2)
    transferring.start()
    try:
        assert not moved_back.wait(0.1), 'Handoff passed a still-running output control'
        native.dispose.assert_not_called()
    finally:
        release.set()
        controlling.join(3)
        transferring.join(3)
    assert not controlling.is_alive() and not transferring.is_alive() and errors == []
    assert service.snapshot(sid, owner)['output_client_id'] == 'new-tab'


def test_control_waiting_for_handoff_uses_new_output_not_disposed_native(domain):
    from playback_sessions import BrowserOutput
    service, sid, owner, media, events, native = domain
    moved = service.handoff(sid, owner, 'native', 0, 'vlc')
    plan = service.select_song(sid, owner, 1, 'select', moved['revision'])
    entered, release, controlled = threading.Event(), threading.Event(), threading.Event()
    original_factory = service._output
    def factory(session_id, owner_key, mode, client):
        output = original_factory(session_id, owner_key, mode, client)
        if isinstance(output, BrowserOutput):
            original_start = output.start
            def start(snapshot):
                entered.set()
                assert release.wait(3)
                return original_start(snapshot)
            output.start = start
        return output
    service._output = factory
    errors = []
    def move():
        try:
            service.handoff(sid, owner, 'browser', plan['revision'], 'browser', 'new-tab')
        except Exception as exc:
            errors.append(exc)
    def pause():
        try:
            service.pause(sid, owner)
        except Exception as exc:
            errors.append(exc)
        finally:
            controlled.set()
    transfer = threading.Thread(target=move)
    control = threading.Thread(target=pause)
    transfer.start()
    assert entered.wait(2)
    control.start()
    try:
        assert not controlled.wait(0.1)
        native.pause.assert_not_called()
    finally:
        release.set()
        transfer.join(3)
        control.join(3)
    assert errors == []
    assert any(event == 'playback_control' and data.get('action') == 'pause'
               and data['output_client_id'] == 'new-tab' for event, data in events)


def test_public_prepare_result_keeps_current_volume_and_eq_profile(domain):
    service, sid, owner, *_ = domain
    service.set_volume(sid, owner, 23)
    service.set_eq(sid, owner, 'bass_boost')
    prepared = service.next(sid, owner, 'next', 0)
    snapshot = service.snapshot(sid, owner)
    assert prepared['context']['volume'] == snapshot['context']['volume'] == 23
    assert prepared['context']['eq'] == snapshot['context']['eq'] == 'bass_boost'
    assert prepared['context']['eq_bands'] == snapshot['context']['eq_bands']


def test_public_command_and_observation_results_keep_audio_profile_and_flags(domain):
    service, sid, owner, *_ = domain
    service.set_volume(sid, owner, 23)
    profile = service.set_eq(sid, owner, 'bass_boost')['context']
    def check(state):
        assert state['context']['volume'] == 23
        assert state['context']['eq'] == 'bass_boost'
        assert state['context']['eq_bands'] == profile['eq_bands']
    plan, started = start(service, sid, owner)
    check(started['session'])
    stopped = service.stop(sid, owner, 'stop', started['session']['revision'])
    check(stopped)
    duplicate = service.stop(sid, owner, 'stop', started['session']['revision'])
    check(duplicate)
    assert duplicate['duplicate']
    selected = service.select_song(sid, owner, 2, 'select', stopped['revision'])
    check(selected)
    pending = service.handoff(sid, owner, 'move', selected['revision'], 'vlc')
    check(pending)
    assert pending['awaiting_confirmation']
    service.ack_quiesce(sid, owner, selected['instance_id'], 'move', 0, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', selected['revision'], 'vlc')
    check(moved)
    service.store.recover_interrupted()
    interrupted = service.store.snapshot(sid, owner)
    restored = service.resume_interrupted(sid, owner, 'resume', interrupted['revision'])
    check(restored)


def test_failed_second_intent_keeps_existing_pending_desktop_reservation(domain):
    service, sid, owner, *_ = domain
    plan, _ = start(service, sid, owner)
    revision = service.snapshot(sid, owner)['revision']
    pending = service.handoff(sid, owner, 'pending-move', revision, 'vlc')
    assert pending['awaiting_confirmation']
    with pytest.raises(ConflictError):
        service.handoff(sid, owner, 'different-move', revision, 'vlc')
    assert service._vlc_session == sid
    with pytest.raises(ConflictError):
        service.create(mode='vlc')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'pending-move', 10, client_id='tab-a')
    assert service.handoff(sid, owner, 'pending-move', revision, 'vlc')['mode'] == 'vlc'


def test_paused_browser_handoff_never_publishes_autoplay_state(domain):
    service, sid, owner, media, events, *_ = domain
    plan, _ = start(service, sid, owner)
    item = plan['items'][0]
    paused = service.observe(sid, owner, 'pause', plan['instance_id'], item['item_id'], 'paused',
                               client_id='tab-a', position_ms=125)['session']
    service.handoff(sid, owner, 'move', paused['revision'], 'browser', 'new-tab')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 125, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', paused['revision'], 'browser', 'new-tab')
    assert moved['status'] == 'paused' and moved['items'][0]['attempt_id'] == item['attempt_id']
    target_states = [data['session'] for event, data in events if event == 'playback_state'
                     and data.get('session', {}).get('instance_id') == moved['instance_id']]
    assert target_states and all(state['status'] == 'paused' for state in target_states)
    assert len(service.context_for(sid, owner).queue.starts) == 1


def test_vanished_browser_grant_is_reclaimed_without_a_quiesce(domain):
    service, sid, owner, media, events, *_ = domain
    plan, started = start(service, sid, owner)
    item = plan['items'][0]
    # The old page never connected a control channel, so it can never confirm.
    service._producer_online = lambda session_id, client: False
    reclaimed = service.handoff(sid, owner, 'reclaim', started['session']['revision'], 'browser', 'new-tab')
    assert 'awaiting_confirmation' not in reclaimed
    assert reclaimed['output_client_id'] == 'new-tab' and reclaimed['status'] == 'ready'
    assert reclaimed['items'][0]['attempt_id'] == item['attempt_id']
    assert reclaimed['instance_id'] != started['session']['instance_id']


def test_stale_handoff_cannot_reclaim_a_vanished_browser(domain):
    service, sid, owner, *_ = domain
    _, started = start(service, sid, owner)
    before = service.snapshot(sid, owner)
    service._producer_online = lambda *args: False
    with pytest.raises(ConflictError):
        service.handoff(sid, owner, 'stale-reclaim', 999999, 'browser', 'new-tab')
    after = service.snapshot(sid, owner)
    assert after['revision'] == before['revision']
    assert after['instance_id'] == before['instance_id']
    assert after['output_client_id'] == before['output_client_id']


def test_live_browser_grant_still_has_to_confirm_its_quiesce(domain):
    service, sid, owner, media, events, *_ = domain
    plan, started = start(service, sid, owner)
    service._producer_online = lambda session_id, client: client == 'tab-a'
    pending = service.handoff(sid, owner, 'move', started['session']['revision'], 'browser', 'new-tab')
    assert pending['awaiting_confirmation']
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 175, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', started['session']['revision'], 'browser', 'new-tab')
    assert moved['output_client_id'] == 'new-tab' and not moved.get('awaiting_confirmation')


def test_paused_native_handoff_starts_only_after_resume_and_keeps_single_listen(domain):
    from db import database
    from engine.vlc_session_output import VlcSessionOutput
    from tests.test_vlc_session_output import FakePlayer
    service, sid, owner, *_ = domain
    player = FakePlayer()
    player.set_volume, player.set_eq_preset, player.set_normalization_enabled = Mock(), Mock(), Mock()
    service._vlc_output_factory = lambda sid, owner, observer: VlcSessionOutput(player, lambda item: 'private.wav', observer)
    plan, _ = start(service, sid, owner)
    paused = service.observe(sid, owner, 'pause', plan['instance_id'], plan['items'][0]['item_id'],
                               'paused', client_id='tab-a', position_ms=125)['session']
    service.handoff(sid, owner, 'move', paused['revision'], 'vlc')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 125, client_id='tab-a')
    moved = service.handoff(sid, owner, 'move', paused['revision'], 'vlc')
    assert moved['status'] == 'paused' and player.calls == []
    assert database.get_song(1)['play_count'] == 1
    service.resume(sid, owner)
    assert len(player.calls) == 1
    player.callback({'instance_id': moved['instance_id'], 'item_id': moved['items'][0]['item_id'],
                     'kind': 'started', 'generation': 1, 'observation_seq': 1, 'position_ms': 0})
    assert player.seeks == [125]
    assert service.snapshot(sid, owner)['status'] == 'playing'
    assert database.get_song(1)['play_count'] == 1
    assert len(service.context_for(sid, owner).queue.starts) == 1


def test_failed_paused_browser_output_is_publicly_paused_while_retryable(domain, monkeypatch):
    from playback_sessions import BrowserOutput
    service, sid, owner, *_ = domain
    plan, _ = start(service, sid, owner)
    paused = service.observe(sid, owner, 'pause', plan['instance_id'], plan['items'][0]['item_id'],
                               'paused', client_id='tab-a', position_ms=125)['session']
    service.handoff(sid, owner, 'move', paused['revision'], 'browser', 'new-tab')
    service.ack_quiesce(sid, owner, plan['instance_id'], 'move', 125, client_id='tab-a')
    monkeypatch.setattr(BrowserOutput, 'start', lambda *args: False)
    failed = service.handoff(sid, owner, 'move', paused['revision'], 'browser', 'new-tab')
    assert service.store.snapshot(sid, owner)['status'] == 'awaiting_output'
    assert failed['status'] == 'paused' and failed['awaiting_output']
    snapshot = service.snapshot(sid, owner)
    assert snapshot['status'] == 'paused' and snapshot['awaiting_output']
    assert snapshot['position_ms'] == 125


def test_shutdown_retains_lease_for_interrupted_generated_plan(domain):
    service, sid, owner, media, *_ = domain
    queue = service.context_for(sid, owner).queue
    queue.prepare_pick = lambda pick: PreparedPlan((MediaItem('song', '/private/effect.wav', 1, 1000),))
    plan, _ = start(service, sid, owner)
    lease = media.register_generated.call_args.args[1]
    service.shutdown()
    assert service.store.snapshot(sid, owner)['status'] == 'interrupted'
    assert all(call.args[0] != lease for call in media.release_session.call_args_list)
