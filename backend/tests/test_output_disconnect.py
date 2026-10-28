"""A vanished browser output retires its grant without ending its listen."""
import asyncio
from unittest.mock import Mock

from db import database
from db.playback_store import PlaybackStore
from playback_api import PlaybackEventHub
from tests.test_playback_sessions import domain, start


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
        hub = PlaybackEventHub()
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
