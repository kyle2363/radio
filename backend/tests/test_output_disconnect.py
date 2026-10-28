"""A vanished browser output retires its grant without ending its listen."""
import asyncio
from unittest.mock import Mock
import pytest

from db import database
from db.playback_store import PlaybackStore
from playback_api import PlaybackEventHub
from tests.test_playback_sessions import domain, start


def test_old_grace_generation_cannot_retire_second_disconnect():
    async def run():
        service = Mock()
        hub = PlaybackEventHub(disconnect_grace=0)
        first, second = object(), object()
        lease = {'lock': asyncio.Lock(), 'users': 1, 'generation': second}
        hub._connection_locks[('s', 'page')] = lease
        # Both sockets have disconnected; the first timer is now runnable.
        await hub._release_after_grace(service, 's', 'owner', 'page', lease, first, 'instance')
        service.interrupt_output.assert_not_called()
        assert not hub._connection_locks
    asyncio.run(run())


def test_grace_cannot_retire_new_instance_for_same_page():
    service = Mock()
    service.snapshot.return_value = {'mode': 'browser', 'output_client_id': 'page', 'instance_id': 'new'}
    PlaybackEventHub._retire_output(service, 's', 'owner', 'page', 'old')
    service.interrupt_output.assert_not_called()


def test_persisted_lease_blocks_restart_reactivation_until_deadline(domain):
    from db.playback_store import ConflictError
    service, sid, owner, *_ = domain
    now = [1000.0]
    service.store._wall_clock = lambda: now[0]
    plan, _ = start(service, sid, owner)
    service.renew_producer_lease(sid, owner, plan['instance_id'], 'tab-a')
    interrupted = service.store.interrupt_session(sid, owner)['session']
    restored_store = PlaybackStore()
    restored_store._wall_clock = lambda: now[0]
    with pytest.raises(ConflictError):
        restored_store.reactivate(sid, owner, 'resume', interrupted['revision'], 'tab-b')
    now[0] += 13
    restored = restored_store.reactivate(sid, owner, 'resume', interrupted['revision'], 'tab-b')
    assert restored['items'][0]['attempt_id'] == plan['items'][0]['attempt_id']


def test_store_disconnect_preserves_attempt_history_and_current_item():
    store = PlaybackStore()
    state = store.create_session('owner', 'browser', output_client_id='old')
    sid = state['id']
    state = store.prepare(sid, 'owner', 'next', 0, [{'kind': 'song', 'song_id': 1, 'duration_ms': 100000}])
    item = state['items'][0]
    store.observe(sid, 'owner', 'start', state['instance_id'], item['item_id'], 'started', client_id='old')
    result = store.interrupt_output(sid, 'owner', state['instance_id'], 'old', position_ms=1234)
    assert result['accepted']
    interrupted = result['session']
    assert interrupted['status'] == 'interrupted' and interrupted['position_ms'] == 1234
    assert interrupted['active_index'] == 0 and interrupted['items'][0]['item_id'] == item['item_id']
    assert interrupted['items'][0]['attempt_id'] == item['attempt_id']
    assert not interrupted['items'][0]['started'] and not interrupted['items'][0]['terminal']
    assert not store.observe(sid, 'owner', 'late', state['instance_id'], item['item_id'], 'completed', client_id='old')['accepted']
    restored = store.reactivate(sid, 'owner', 'restore', interrupted['revision'], 'new')
    store.observe(sid, 'owner', 'restart', restored['instance_id'], item['item_id'], 'started', client_id='new')
    assert database.get_song(1)['play_count'] == 1
    assert database.get_song(1)['complete_count'] == database.get_song(1)['skip_count'] == 0
    with database.get_connection_cm() as conn:
        assert conn.execute('SELECT outcome FROM playback_attempts WHERE id=?', (item['attempt_id'],)).fetchone()[0] is None


def test_service_disconnect_uses_latest_progress_without_auto_advance(domain):
    service, sid, owner, *_ = domain
    state, _ = start(service, sid, owner)
    item = state['items'][0]
    service.progress(sid, owner, state['instance_id'], item['item_id'], 1, 1234, client_id='tab-a')
    context = service.context_for(sid, owner)
    queued = list(context.queue.items)
    service._schedule_next = Mock()
    result = service.interrupt_output(sid, owner, state['instance_id'], 'tab-a')
    assert result['accepted'] and result['session']['position_ms'] == 1234
    assert context.queue.items == queued
    assert not service.interrupt_output(sid, owner, state['instance_id'], 'tab-a')['accepted']
    service._schedule_next.assert_not_called()


def test_old_browser_disconnect_after_native_handoff_is_inert(domain):
    service, sid, owner, *_ = domain
    state, _ = start(service, sid, owner)
    revision = service.snapshot(sid, owner)['revision']
    service.handoff(sid, owner, 'move', revision, 'vlc')
    service.ack_quiesce(sid, owner, state['instance_id'], 'move', 25, client_id='tab-a')
    native = service.handoff(sid, owner, 'move', revision, 'vlc')
    result = service.interrupt_output(sid, owner, state['instance_id'], 'tab-a')
    assert not result['accepted']
    assert result['session']['mode'] == 'vlc'
    assert result['session']['instance_id'] == native['instance_id']


def test_public_interrupted_observation_preserves_current_attempt_without_advance(domain):
    service, sid, owner, *_ = domain
    state, _ = start(service, sid, owner)
    item = state['items'][0]
    service._schedule_next = Mock()
    result = service.observe(sid, owner, 'network', state['instance_id'], item['item_id'], 'interrupted', position_ms=250, client_id='tab-a')
    assert result['accepted'] and result['session']['status'] == 'interrupted'
    assert result['session']['active_index'] == 0
    assert result['session']['items'][0]['attempt_id'] == item['attempt_id']
    assert not result['session']['items'][0]['terminal']
    service._schedule_next.assert_not_called()


def test_hub_old_same_client_disconnect_cannot_interrupt_replacement():
    from starlette.websockets import WebSocket
    async def run():
        service = Mock()
        service.snapshot.return_value = {'mode': 'browser', 'output_client_id': 'page', 'instance_id': 'instance'}
        service.interrupt_output.return_value = {'accepted': True}
        hub = PlaybackEventHub(disconnect_grace=0)
        async def connect():
            incoming, sent = asyncio.Queue(), asyncio.Queue()
            async def receive(): return await incoming.get()
            async def send(message): await sent.put(message)
            scope = {'type': 'websocket', 'path': '/events', 'headers': [], 'query_string': b''}
            task = asyncio.create_task(hub.websocket(WebSocket(scope, receive, send), service))
            await incoming.put({'type': 'websocket.connect'})
            await sent.get()
            await incoming.put({'type': 'websocket.receive', 'text': '{"session_id":"s","owner_key":"owner","client_id":"page"}'})
            await sent.get()
            return incoming, task
        first, old = await connect()
        second, new = await connect()
        await first.put({'type': 'websocket.disconnect', 'code': 1000})
        await old
        service.interrupt_output.assert_not_called()
        await second.put({'type': 'websocket.disconnect', 'code': 1000})
        await new
        service.interrupt_output.assert_called_once_with('s', 'owner', 'instance', 'page')
        assert not hub._connections and not hub._connection_locks
    asyncio.run(run())


def _subscriber(hub, service, client='page'):
    from starlette.websockets import WebSocket
    incoming, sent = asyncio.Queue(), asyncio.Queue()

    async def receive():
        return await incoming.get()

    async def send(message):
        await sent.put(message)

    scope = {'type': 'websocket', 'path': '/events', 'headers': [], 'query_string': b''}
    task = asyncio.create_task(hub.websocket(WebSocket(scope, receive, send), service))
    return incoming, sent, task


async def _join(incoming, sent, client='page'):
    await incoming.put({'type': 'websocket.connect'})
    await sent.get()
    await incoming.put({'type': 'websocket.receive',
                        'text': f'{{"session_id":"s","owner_key":"owner","client_id":"{client}"}}'})
    await sent.get()


def test_hub_grace_delays_retirement_of_an_absent_page():
    async def run():
        service = Mock()
        service.snapshot.return_value = {'mode': 'browser', 'output_client_id': 'page', 'instance_id': 'instance'}
        hub = PlaybackEventHub(disconnect_grace=0.05)
        incoming, sent, task = _subscriber(hub, service)
        await _join(incoming, sent)
        await incoming.put({'type': 'websocket.disconnect', 'code': 1006})
        await task
        # A closed control socket is not yet proof the station stopped playing.
        service.interrupt_output.assert_not_called()
        await asyncio.sleep(0.2)
        service.interrupt_output.assert_called_once_with('s', 'owner', 'instance', 'page')
        assert not hub._retiring
    asyncio.run(run())


def test_hub_reconnect_inside_grace_keeps_the_same_output():
    async def run():
        service = Mock()
        service.snapshot.return_value = {'mode': 'browser', 'output_client_id': 'page', 'instance_id': 'instance'}
        hub = PlaybackEventHub(disconnect_grace=0.1)
        first, sent, first_task = _subscriber(hub, service)
        await _join(first, sent)
        await first.put({'type': 'websocket.disconnect', 'code': 1006})
        await first_task
        second, reply, second_task = _subscriber(hub, service)
        await _join(second, reply)
        await asyncio.sleep(0.2)
        service.interrupt_output.assert_not_called()
        await second.put({'type': 'websocket.disconnect', 'code': 1006})
        await second_task
        await asyncio.sleep(0.2)
        service.interrupt_output.assert_called_once_with('s', 'owner', 'instance', 'page')
        assert not hub._retiring and not hub._connections
    asyncio.run(run())
