"""Session-owned sleep behavior with deterministic time and inert timers."""
import threading
from unittest.mock import Mock

from tests.test_playback_sessions import domain, start
from engine.playback_plan import MediaItem, PreparedPlan


def timer_clock(service):
    timers, now = [], [1000]
    class Timer:
        def __init__(self, delay, callback, args):
            self.delay, self.callback, self.args = delay, callback, args
            timers.append(self)
        def start(self): pass
        def cancel(self): pass
    service._timer_factory = Timer
    service._wall_clock = lambda: now[0]
    return now, timers


def test_zero_stops_after_song_not_speech_without_advancing(domain):
    service, sid, owner, *_ = domain
    now, timers = timer_clock(service)
    context = service.context_for(sid, owner)
    context.preferences['auto_advance'] = True
    plan = PreparedPlan((MediaItem('speech', '/private/intro.wav', None, 50), MediaItem('song', '/private/song.mp3', 1, 1000)))
    state = service.select_prepared(sid, owner, plan, 'pick', 0)
    timer = service.set_sleep_timer(sid, owner, 0)
    assert timer['end_after_current'] is True and timer['expires_at'] is None
    assert timers == []
    service._schedule_next = Mock()
    for index, item in enumerate(state['items']):
        service.observe(sid, owner, f'start-{index}', state['instance_id'], item['item_id'], 'started', client_id='tab-a')
        result = service.observe(sid, owner, f'end-{index}', state['instance_id'], item['item_id'], 'completed', client_id='tab-a')
        if index == 0:
            assert service.get_sleep_timer(sid, owner)['end_after_current']
            assert result['session']['status'] != 'stopped'
    assert service.snapshot(sid, owner)['status'] == 'stopped'
    assert service.get_sleep_timer(sid, owner) is None
    service._schedule_next.assert_not_called()


def test_chill_threshold_is_session_owned_and_expiry_restores_volume(domain):
    service, sid, owner, *_ = domain
    now, timers = timer_clock(service)
    other = service.create(client_id='other')
    context = service.context_for(sid, owner)
    start(service, sid, owner)
    result = service.set_sleep_timer(sid, owner, 20, fade_seconds=10, switch_to_chill=True)
    assert result['switch_to_chill'] is True
    assert timers[0].delay == 600
    assert service.snapshot(sid, owner)['mood'] == 'neutral'
    class Cancel:
        def is_set(self): return False
        def wait(self, seconds):
            now[0] = 2201
            return False
    cancel = Cancel()
    context.timer_cancel = cancel
    now[0] = 1600
    service._run_sleep(sid, owner, context.preferences['sleep_timer'].copy(), cancel)
    assert service.snapshot(sid, owner)['mood'] == 'chill'
    assert service.snapshot(other['session']['id'], other['owner_key'])['mood'] == 'neutral'
    assert service.snapshot(sid, owner)['status'] == 'stopped'
    assert context.output.get_volume() == context.preferences['volume']


def test_cancel_restores_session_volume_without_resuming(domain):
    service, sid, owner, *_ = domain
    timer_clock(service)
    context = service.context_for(sid, owner)
    service.set_sleep_timer(sid, owner, 5, switch_to_chill=False)
    context.output.set_volume(1)
    service.cancel_sleep_timer(sid, owner)
    assert context.output.get_volume() == context.preferences['volume']
    assert service.snapshot(sid, owner)['status'] == 'idle'


def test_stale_expired_worker_cannot_stop_replacement_timer(domain):
    service, sid, owner, *_ = domain
    now, timers = timer_clock(service)
    service.set_sleep_timer(sid, owner, 1, switch_to_chill=False)
    old = timers[0]
    service.set_sleep_timer(sid, owner, 20, switch_to_chill=False)
    now[0] = 1061
    old.callback(*old.args)
    assert service.get_sleep_timer(sid, owner)['expires_at'] == 2200
    assert service.snapshot(sid, owner)['status'] == 'idle'


def test_timer_expiry_and_handoff_share_transport_before_context_order(domain):
    service, sid, owner, *_ = domain
    timer_clock(service)
    context = service.context_for(sid, owner)
    state, _ = start(service, sid, owner)
    service.set_sleep_timer(sid, owner, 20, switch_to_chill=False)
    runtime = service.coordinator._runtime(sid, owner)
    quiescing, release, timer_attempted = threading.Event(), threading.Event(), threading.Event()
    errors = []
    original = context.lock
    class OrderedLock:
        def __enter__(self):
            if threading.current_thread().name == 'test-sleep-expiry' and not runtime.transport_lock._is_owned():
                raise AssertionError('Timer acquired context before output transport')
            return original.__enter__()
        def __exit__(self, *args): return original.__exit__(*args)
    context.lock = OrderedLock()
    def quiesce(instance_id, command_id):
        quiescing.set()
        assert release.wait(2)
        return {'confirmed': True, 'instance_id': instance_id, 'command_id': command_id, 'position_ms': 25}
    context.output.quiesce = quiesce
    def handoff():
        try: service.handoff(sid, owner, 'move', service.snapshot(sid, owner)['revision'], 'vlc')
        except Exception as exc: errors.append(exc)
    def expire():
        timer_attempted.set()
        try: service._finish_sleep(sid, owner, context)
        except Exception as exc: errors.append(exc)
    moving = threading.Thread(target=handoff)
    sleeping = threading.Thread(target=expire, name='test-sleep-expiry')
    moving.start()
    assert quiescing.wait(2)
    sleeping.start()
    assert timer_attempted.wait(2)
    release.set()
    moving.join(2)
    sleeping.join(2)
    assert not moving.is_alive() and not sleeping.is_alive()
    assert errors == []
    assert service.snapshot(sid, owner)['status'] == 'stopped'


def test_shutdown_releases_registry_before_waiting_on_timer_context(domain):
    from db.playback_store import ConflictError
    service, sid, owner, *_ = domain
    context = service.context_for(sid, owner)
    original = context.lock
    timer_holds_context, cancellation_attempted = threading.Event(), threading.Event()
    errors = []
    class CheckedLock:
        def __enter__(self):
            if threading.current_thread().name == 'test-session-shutdown':
                cancellation_attempted.set()
                if service._lock._is_owned():
                    raise AssertionError('Shutdown waited for timer context while holding session registry')
            return original.__enter__()
        def __exit__(self, *args): return original.__exit__(*args)
    context.lock = CheckedLock()
    def timer():
        try:
            with context.lock:
                timer_holds_context.set()
                assert cancellation_attempted.wait(2)
                try:
                    service.set_mood(sid, owner, 'chill')
                except ConflictError:
                    pass  # Closing admission retires this in-flight worker.
        except Exception as exc:
            errors.append(exc)
    def shutdown():
        try: service.shutdown()
        except Exception as exc: errors.append(exc)
    worker = threading.Thread(target=timer, daemon=True)
    closing = threading.Thread(target=shutdown, name='test-session-shutdown', daemon=True)
    worker.start()
    assert timer_holds_context.wait(2)
    closing.start()
    worker.join(3)
    closing.join(3)
    assert not worker.is_alive() and not closing.is_alive()
    assert errors == []


def test_existing_attach_snapshots_without_registry_context_inversion(domain):
    service, sid, owner, *_ = domain
    context = service.context_for(sid, owner)
    original = context.lock
    held, snapshot_attempted = threading.Event(), threading.Event()
    errors = []
    class CheckedLock:
        def __enter__(self):
            if threading.current_thread().name == 'test-existing-attach':
                snapshot_attempted.set()
                if service._lock._is_owned():
                    raise AssertionError('Attach snapshots context while holding registry')
            return original.__enter__()
        def __exit__(self, *args): return original.__exit__(*args)
    context.lock = CheckedLock()
    def timer():
        try:
            with context.lock:
                held.set()
                assert snapshot_attempted.wait(2)
                service.context_for(sid, owner)
        except Exception as exc: errors.append(exc)
    def attach():
        try: service.attach(sid, owner)
        except Exception as exc: errors.append(exc)
    worker = threading.Thread(target=timer, daemon=True)
    attaching = threading.Thread(target=attach, name='test-existing-attach', daemon=True)
    worker.start()
    assert held.wait(2)
    attaching.start()
    worker.join(3)
    attaching.join(3)
    assert not worker.is_alive() and not attaching.is_alive()
    assert errors == []
