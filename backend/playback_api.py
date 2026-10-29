"""The HTTP boundary and its event hub. Controller capabilities travel only in
the owner header or the first WebSocket frame, never in resource URLs.
Disconnect removes that subscriber immediately and retires the browser grant
only after a grace period in which the same page may reconnect.
"""
import asyncio
from copy import deepcopy
import threading
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocketDisconnect
from db.playback_store import ConflictError


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Create(Payload):
    mode: Literal['browser'] = 'browser'
    client_id: str | None = Field(default=None, min_length=1, max_length=128)


class Command(Payload):
    command_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class Select(Command):
    song_id: int = Field(gt=0)


class Observation(Payload):
    event_id: str = Field(min_length=1, max_length=128)
    instance_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=128)
    kind: Literal['started', 'paused', 'resumed', 'completed', 'skipped', 'failed', 'interrupted', 'hearted', 'thumbed_down', 'replayed']
    position_ms: int = Field(default=0, ge=0)
    client_id: str = Field(min_length=1, max_length=128)


class Position(Payload):
    instance_id: str
    item_id: str
    sequence: int = Field(ge=0)
    position_ms: int = Field(ge=0)
    client_id: str = Field(min_length=1, max_length=128)


class ProducerLease(Payload):
    instance_id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)


class Seek(Payload):
    position_ms: int = Field(ge=0)


class Volume(Payload):
    volume: int = Field(ge=0, le=100)


class Equalizer(Payload):
    preset: str = Field(min_length=1, max_length=64)


class Normalization(Payload):
    enabled: bool


class Reranking(Payload):
    enabled: bool


class Mood(Payload):
    mood: Literal['chill', 'neutral', 'energetic', 'focus', 'melancholy', 'discovery']


class Vibe(Payload):
    song_id: int | None = Field(default=None, gt=0)


class Handoff(Command):
    target_mode: Literal['browser', 'vlc']
    target_client_id: str | None = Field(default=None, min_length=1, max_length=128)


class Reactivate(Command):
    client_id: str | None = Field(default=None, min_length=1, max_length=128)


class Ack(Payload):
    instance_id: str
    command_id: str
    position_ms: int = Field(ge=0)
    client_id: str = Field(min_length=1, max_length=128)


class QueueAdd(Payload):
    song_ids: list[int] = Field(min_length=1, max_length=100)
    position: Literal['front', 'back'] = 'back'


class QueueRemove(Payload):
    song_id: int = Field(gt=0)


class QueueOrder(Payload):
    order: list[int] = Field(max_length=100)


class Timer(Payload):
    minutes: int | float = Field(ge=0, le=1440, allow_inf_nan=False)
    fade_seconds: int | float = Field(default=10, ge=0, le=30, allow_inf_nan=False)
    switch_to_chill: bool = True


async def _call(method, *args, **kwargs):
    try:
        return await asyncio.to_thread(method, *args, **kwargs)
    except (PermissionError, KeyError, ConflictError, ValueError, RuntimeError) as exc:
        code = next(code for kind, code in [(PermissionError, 403), (KeyError, 404),
                    (ConflictError, 409), (ValueError, 400), (RuntimeError, 503)] if isinstance(exc, kind))
        raise HTTPException(code, {403: 'Playback access denied', 404: 'Session not found',
                            409: 'Playback state conflict', 400: 'Invalid playback request',
                            503: 'Playback unavailable'}[code]) from None


def _owner(value):
    if not value:
        raise HTTPException(403, 'Playback owner header required')
    return value


class PlaybackEventHub:
    def __init__(self, max_pending=64, disconnect_grace=10.0):
        if max_pending < 1:
            raise ValueError('Positive event bound required')
        if disconnect_grace < 0:
            raise ValueError('Disconnect grace must be nonnegative')
        self.max_pending = max_pending
        # A dropped control socket is not proof that the browser stopped
        # playing: reloads, dev-proxy hiccups and sleeping laptops all close it.
        # Wait this long for the same page to reconnect before retiring output.
        self.disconnect_grace = disconnect_grace
        self._retiring = set()
        self._lock = threading.RLock()
        self._subscribers = {}
        self._connections = {}
        self._connection_locks = {}

    @property
    def subscriber_count(self):
        with self._lock:
            return sum(len(value) for value in self._subscribers.values())

    def is_connected(self, session_id, client_id):
        """True while this exact page holds a live control channel."""
        with self._lock:
            return (session_id, client_id) in self._connections

    def subscribe(self, session_id):
        queue = asyncio.Queue(self.max_pending)
        with self._lock:
            self._subscribers.setdefault(session_id, {})[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, session_id, queue):
        with self._lock:
            entries = self._subscribers.get(session_id, {})
            entries.pop(queue, None)
            if not entries:
                self._subscribers.pop(session_id, None)

    @staticmethod
    def _can_view(state, client):
        # Owner capability authorizes control of native output; it never grants
        # native observation rights. Browser output additionally fences the
        # producer client so replacement grants cannot keep old pages alive.
        return state.get('mode') == 'vlc' or (state.get('mode') == 'browser' and state.get('output_client_id') == client)

    def emit(self, session_id, event, data):
        payload = {**deepcopy(data), 'event': event, 'session_id': session_id}
        with self._lock:
            targets = list(self._subscribers.get(session_id, {}).items())
        for queue, loop in targets:
            def deliver(queue=queue):
                with self._lock:
                    if queue not in self._subscribers.get(session_id, {}):
                        return
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(deepcopy(payload))
            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                self.unsubscribe(session_id, queue)

    async def websocket(self, websocket, service):
        await websocket.accept()
        queue, sid, receive = None, None, None
        token, client, owner, disconnected = None, None, None, False
        lease = None
        try:
            frame = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            if not isinstance(frame, dict) or set(frame) != {'session_id', 'owner_key', 'client_id'}:
                raise HTTPException(403)
            sid, owner, client = (frame[key] for key in ('session_id', 'owner_key', 'client_id'))
            if any(not isinstance(v, str) or not 1 <= len(v) <= 256 for v in (sid, owner, client)):
                raise HTTPException(403)
            state = await _call(service.snapshot, sid, owner)
            if not self._can_view(state, client):
                raise HTTPException(403)
            token = object()
            key = (sid, client)
            lease = self._connection_locks.setdefault(key, {'lock': asyncio.Lock(), 'users': 0})
            lease['users'] += 1
            async with lease['lock']:
                self._connections[(sid, client)] = token
                lease['generation'] = token
            queue = self.subscribe(sid)
            state = await _call(service.snapshot, sid, owner)
            if not self._can_view(state, client):
                raise HTTPException(403)
            await websocket.send_json({'event': 'playback_state', 'session_id': sid, 'session': state})
            receive = asyncio.create_task(websocket.receive())
            while True:
                outgoing = asyncio.create_task(queue.get())
                try:
                    done, _ = await asyncio.wait((receive, outgoing), return_when=asyncio.FIRST_COMPLETED)
                    if receive in done:
                        message = receive.result()
                        if message['type'] == 'websocket.disconnect':
                            disconnected = True
                            break
                        receive = asyncio.create_task(websocket.receive())
                    if outgoing in done:
                        state = await _call(service.snapshot, sid, owner)
                        if not self._can_view(state, client):
                            raise HTTPException(403)
                        await websocket.send_json(outgoing.result())
                finally:
                    outgoing.cancel()
                    await asyncio.gather(outgoing, return_exceptions=True)
        except (HTTPException, ValueError, TypeError, asyncio.TimeoutError):
            await websocket.close(code=1008)
        except WebSocketDisconnect:
            disconnected = True
        finally:
            if receive is not None:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)
            if queue is not None:
                self.unsubscribe(sid, queue)
            if token is not None:
                retired = False
                try:
                    # Serialize replacement registration and disconnect
                    # cleanup without holding the thread lock needed by
                    # service emits.
                    async with lease['lock']:
                        if self._connections.get((sid, client)) is token:
                            self._connections.pop((sid, client), None)
                            retired = True
                            state = await _call(service.snapshot, sid, owner)
                    if retired and disconnected:
                        await self._release(service, sid, owner, client, lease, token, state.get('instance_id'))
                except (ConflictError, KeyError, PermissionError, RuntimeError):
                    pass
                finally:
                    lease['users'] -= 1
                    if not lease['users']:
                        self._connection_locks.pop((sid, client), None)

    async def _release(self, service, sid, owner, client, lease, token, instance):
        """Retire the grant of a vanished page, unless it reconnects in time."""
        lease['users'] += 1
        if self.disconnect_grace > 0:
            task = asyncio.create_task(self._release_after_grace(service, sid, owner, client, lease, token, instance))
            # The loop keeps only weak references to tasks.
            self._retiring.add(task)
            task.add_done_callback(self._retiring.discard)
            return
        await self._release_after_grace(service, sid, owner, client, lease, token, instance)

    async def _release_after_grace(self, service, sid, owner, client, lease, token, instance):
        try:
            await asyncio.sleep(self.disconnect_grace)
            async with lease['lock']:
                if (lease.get('generation') is not token or (sid, client) in self._connections):
                    return
                await asyncio.to_thread(self._retire_output, service, sid, owner, client, instance)
        except asyncio.CancelledError:
            raise
        except (ConflictError, KeyError, PermissionError, RuntimeError):
            pass
        finally:
            lease['users'] -= 1
            if not lease['users']:
                self._connection_locks.pop((sid, client), None)

    @staticmethod
    def _retire_output(service, sid, owner, client, instance):
        state = service.snapshot(sid, owner)
        if (state.get('mode') == 'browser' and state.get('output_client_id') == client
                and state.get('instance_id') == instance):
            service.interrupt_output(sid, owner, instance, client)


def create_playback_router(service, hub=None, desktop_claim=None):
    router = APIRouter(prefix='/api/playback/sessions')

    @router.post('')
    async def create(body: Create):
        return await _call(service.create, mode='browser', client_id=body.client_id, warm_start=True)

    if desktop_claim is not None:
        @router.post('/desktop/claim')
        async def claim():
            return await _call(desktop_claim)

    @router.get('/{session_id}')
    async def snapshot(session_id: str, owner: str | None = Header(default=None, alias='X-Radio-Playback-Owner')):
        return await _call(service.snapshot, session_id, _owner(owner))

    @router.get('/{session_id}/queue')
    async def queue(session_id: str, owner: str | None = Header(default=None, alias='X-Radio-Playback-Owner')):
        state = await _call(service.snapshot, session_id, _owner(owner))
        return {'queue': state.get('queue', [])}

    def action(path, method, model=None, verb='POST'):
        if model is None:
            async def endpoint(session_id: str, owner: str | None = Header(default=None, alias='X-Radio-Playback-Owner')):
                return await _call(getattr(service, method), session_id, _owner(owner))
        else:
            async def endpoint(session_id: str, body: model, owner: str | None = Header(default=None, alias='X-Radio-Playback-Owner')):
                return await _call(getattr(service, method), session_id, _owner(owner), **body.model_dump())
        router.add_api_route('/{session_id}' + path, endpoint, methods=[verb], name=method)

    for path, method, model in [('/next', 'next', Command), ('/select', 'select_song', Select),
            ('/replay', 'replay', Command),
            ('/events', 'observe', Observation), ('/position', 'progress', Position),
            ('/pause', 'pause', None), ('/resume', 'resume', None), ('/seek', 'seek', Seek),
            ('/volume', 'set_volume', Volume), ('/eq', 'set_eq', Equalizer),
            ('/normalization', 'set_normalization', Normalization), ('/mood', 'set_mood', Mood),
            ('/reranking', 'set_reranking', Reranking),
            ('/vibe', 'set_vibe', Vibe), ('/queue/add', 'queue_add', QueueAdd),
            ('/queue/remove', 'queue_remove', QueueRemove), ('/queue/clear', 'queue_clear', None),
            ('/queue/reorder', 'queue_reorder', QueueOrder), ('/stop', 'stop', Command),
            ('/handoff', 'handoff', Handoff), ('/handoff/cancel', 'cancel_handoff', Handoff),
            ('/lease', 'renew_producer_lease', ProducerLease), ('/ack', 'ack_quiesce', Ack),
            ('/reactivate', 'resume_interrupted', Reactivate), ('/close', 'close', Command),
            ('/timer', 'set_sleep_timer', Timer)]:
        action(path, method, model)
    action('/timer', 'get_sleep_timer', verb='GET')
    action('/timer', 'cancel_sleep_timer', verb='DELETE')
    if hub is not None:
        @router.websocket('/events')
        async def events(websocket: WebSocket):
            await hub.websocket(websocket, service)
    return router
