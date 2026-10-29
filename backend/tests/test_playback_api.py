"""In-process API boundary checks, using inert deterministic domain doubles."""
import asyncio
from unittest.mock import Mock

import httpx
from fastapi import FastAPI
from db.playback_store import ConflictError
from playback_api import create_playback_router, PlaybackEventHub


def request(service, path, body=None, owner='secret', method='POST'):
    app = FastAPI()
    app.include_router(create_playback_router(service))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            return await client.request(method, path, json=body, headers={} if owner is None else {'X-Radio-Playback-Owner': owner})
    return asyncio.run(run())


def test_browser_creation_and_native_rejection():
    service = Mock()
    service.create.return_value = {'owner_key': 'once', 'session': {'id': 's'}}
    assert request(service, '/api/playback/sessions', {'client_id': 'browser'}).status_code == 200
    service.create.assert_called_once_with(mode='browser', client_id='browser', warm_start=True)
    assert request(service, '/api/playback/sessions', {'mode': 'vlc'}).status_code == 422


def test_owner_header_and_command_contract():
    service = Mock()
    service.next.return_value = {'revision': 2}
    path = '/api/playback/sessions/s/next'
    body = {'command_id': 'c', 'expected_revision': 1}
    assert request(service, path, body, owner=None).status_code == 403
    assert request(service, path, body).json() == {'revision': 2}
    service.next.assert_called_once_with('s', 'secret', command_id='c', expected_revision=1)


def test_reranking_session_toggle_requires_owner_and_strict_boolean():
    service = Mock()
    service.set_reranking.return_value = {'preferences': {'settings': {'llm_reranking_enabled': 'false'}}}
    path = '/api/playback/sessions/s/reranking'
    assert request(service, path, {'enabled': False}, owner=None).status_code == 403
    assert request(service, path, {'enabled': 'false'}).status_code == 422
    assert request(service, path, {'enabled': False}).status_code == 200
    service.set_reranking.assert_called_once_with('s', 'secret', enabled=False)


def test_explicit_replay_uses_idempotent_command_contract():
    service = Mock()
    service.replay.return_value = {'revision': 3, 'duplicate': False}
    path = '/api/playback/sessions/s/replay'
    body = {'command_id': 'replay-1', 'expected_revision': 2}
    assert request(service, path, body, owner=None).status_code == 403
    assert request(service, path, body).json() == {'revision': 3, 'duplicate': False}
    service.replay.assert_called_once_with('s', 'secret', command_id='replay-1', expected_revision=2)


def test_native_events_and_unknown_fields_rejected():
    service = Mock()
    body = {'event_id': 'e', 'instance_id': 'i', 'item_id': 't', 'kind': 'started', 'client_id': 'b', 'producer': 'native'}
    assert request(service, '/api/playback/sessions/s/events', body).status_code == 422
    service.observe.assert_not_called()


def test_browser_pause_events_and_normalization():
    service = Mock()
    service.observe.return_value = {'accepted': True}
    service.set_normalization.return_value = {'normalization': False}
    body = {'event_id': 'e', 'instance_id': 'i', 'item_id': 't', 'kind': 'paused', 'client_id': 'b'}
    assert request(service, '/api/playback/sessions/s/events', body).status_code == 200
    assert request(service, '/api/playback/sessions/s/normalization', {'enabled': False}).status_code == 200
    for kind in ('thumbed_down', 'replayed'):
        assert request(service, '/api/playback/sessions/s/events', {**body, 'kind': kind}).status_code == 200


def test_timer_accepts_end_after_current_and_explicit_chill_flag():
    service = Mock()
    service.set_sleep_timer.return_value = {'end_after_current': True}
    body = {'minutes': 0, 'fade_seconds': 0, 'switch_to_chill': False}
    assert request(service, '/api/playback/sessions/s/timer', body).status_code == 200
    service.set_sleep_timer.assert_called_once_with('s', 'secret', **body)


def test_service_errors_are_generic_and_mapped():
    for exception, status in [(PermissionError('PRIVATE'), 403), (KeyError('PRIVATE'), 404),
                              (ConflictError('PRIVATE'), 409), (ValueError('PRIVATE'), 400), (RuntimeError('PRIVATE'), 503)]:
        service = Mock()
        service.snapshot.side_effect = exception
        response = request(service, '/api/playback/sessions/s', method='GET')
        assert response.status_code == status
        assert 'PRIVATE' not in response.text


def test_hub_bounds_and_scopes_threadsafe_delivery():
    async def run():
        hub = PlaybackEventHub(max_pending=2)
        a = hub.subscribe('a')
        b = hub.subscribe('b')
        await asyncio.to_thread(hub.emit, 'a', 'changed', {'n': 1})
        await asyncio.sleep(0)
        assert (await a.get())['n'] == 1
        assert b.empty()
        for n in range(4):
            hub.emit('a', 'changed', {'n': n})
        await asyncio.sleep(0)
        assert a.qsize() == 2
        hub.unsubscribe('a', a)
        hub.unsubscribe('b', b)
        assert hub.subscriber_count == 0
    asyncio.run(run())


def test_websocket_initial_snapshot_and_replacement_grant_cleanup():
    from starlette.websockets import WebSocket
    async def run():
        service = Mock()
        state = {'mode': 'browser', 'output_client_id': 'old'}
        service.snapshot.side_effect = lambda *args: dict(state)
        hub = PlaybackEventHub()
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        async def receive():
            return await incoming.get()
        async def send(message):
            await sent.put(message)
        scope = {'type': 'websocket', 'path': '/events', 'headers': [], 'query_string': b''}
        task = asyncio.create_task(hub.websocket(WebSocket(scope, receive, send), service))
        await incoming.put({'type': 'websocket.connect'})
        assert (await sent.get())['type'] == 'websocket.accept'
        await incoming.put({'type': 'websocket.receive', 'text': '{"session_id":"s","owner_key":"secret","client_id":"old"}'})
        assert 'playback_state' in (await sent.get())['text']
        assert hub.subscriber_count == 1
        state['output_client_id'] = 'replacement'
        hub.emit('s', 'changed', {})
        assert (await sent.get()) == {'type': 'websocket.close', 'code': 1008, 'reason': ''}
        await task
        assert hub.subscriber_count == 0
        service.stop.assert_not_called()
        service.close.assert_not_called()
    asyncio.run(run())


def test_owner_controller_can_view_vlc_without_becoming_producer():
    from starlette.websockets import WebSocket
    async def run():
        service = Mock()
        service.snapshot.return_value = {'mode': 'vlc', 'output_client_id': None}
        hub = PlaybackEventHub()
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        async def receive():
            return await incoming.get()
        async def send(message):
            await sent.put(message)
        task = asyncio.create_task(hub.websocket(WebSocket({'type': 'websocket', 'path': '/events', 'headers': [], 'query_string': b''}, receive, send), service))
        await incoming.put({'type': 'websocket.connect'})
        await sent.get()
        await incoming.put({'type': 'websocket.receive', 'text': '{"session_id":"s","owner_key":"secret","client_id":"controller"}'})
        first = await sent.get()
        assert first['type'] == 'websocket.send'
        hub.emit('s', 'playback_state', {'session': {'mode': 'vlc'}})
        assert (await sent.get())['type'] == 'websocket.send'
        await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
        await task
        assert hub.subscriber_count == 0
        service.observe.assert_not_called()
    asyncio.run(run())
