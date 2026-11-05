"""Bounded, per-client delivery for legacy broadcast WebSockets."""

import asyncio
import inspect


class _Client:
    def __init__(self, websocket, max_pending):
        self.websocket = websocket
        self.queue = asyncio.Queue(maxsize=max_pending)
        self.writer = None
        self.retiring = False


class ClientDelivery:
    """Serialize sends per WebSocket without letting a slow peer block others.

    ``publish`` is synchronous and nonblocking. Direct ``send_text`` and
    ``send_json`` calls share the same queue and await their send completion.
    """

    def __init__(self, on_disconnect=None, max_pending=32, timeout=3.0):
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self.on_disconnect = on_disconnect
        self.max_pending = max_pending
        self.timeout = timeout
        self.clients = {}
        self.tasks = set()
        self._loop = None
        self._closed = False

    def _track(self, task):
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def _get_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("ClientDelivery cannot span event loops")
        return loop

    def add(self, websocket):
        loop = self._get_loop()
        if self._closed:
            raise RuntimeError("ClientDelivery is closed")
        if websocket in self.clients:
            return
        client = _Client(websocket, self.max_pending)
        self.clients[websocket] = client
        client.writer = self._track(loop.create_task(self._writer(client)))

    def remove(self, websocket):
        """Remove a client and schedule its writer/socket cleanup."""
        self._get_loop()
        client = self.clients.get(websocket)
        if client is not None:
            self._schedule_retirement(client, 1000)

    def _schedule_retirement(self, client, code):
        if not client.retiring:
            client.retiring = True
            self._track(self._get_loop().create_task(self._retire(client, code)))

    def publish(self, message):
        """Enqueue a JSON event for every client, evicting full queues."""
        loop = self._get_loop()
        if self._closed:
            return
        for client in tuple(self.clients.values()):
            if client.retiring:
                continue
            try:
                client.queue.put_nowait(("json", message, None))
            except asyncio.QueueFull:
                self._schedule_retirement(client, 1013)

    async def send_text(self, websocket, message):
        await self._send(websocket, "text", message)

    async def send_json(self, websocket, message):
        await self._send(websocket, "json", message)

    async def _send(self, websocket, kind, message):
        loop = self._get_loop()
        client = self.clients.get(websocket)
        if client is None or client.retiring or self._closed:
            raise RuntimeError("WebSocket client is not connected")
        completion = loop.create_future()
        try:
            client.queue.put_nowait((kind, message, completion))
        except asyncio.QueueFull:
            completion.cancel()
            self._schedule_retirement(client, 1013)
            raise RuntimeError("WebSocket client delivery queue is full")
        try:
            await completion
        except asyncio.CancelledError:
            if not completion.done():
                completion.cancel()
            raise

    async def _writer(self, client):
        try:
            while True:
                kind, message, completion = await client.queue.get()
                try:
                    sender = client.websocket.send_text if kind == "text" else client.websocket.send_json
                    await asyncio.wait_for(sender(message), timeout=self.timeout)
                except asyncio.CancelledError:
                    self._reject(completion, RuntimeError("WebSocket client disconnected"))
                    raise
                except Exception as exc:
                    self._reject(completion, exc)
                    await self._retire(client, 1013)
                    return
                else:
                    if completion is not None and not completion.done():
                        completion.set_result(None)
                finally:
                    client.queue.task_done()
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _reject(completion, error):
        if completion is not None and not completion.done():
            completion.set_exception(error)

    async def _retire(self, client, close_code):
        websocket = client.websocket
        if self.clients.get(websocket) is not client:
            return
        self.clients.pop(websocket, None)
        current = asyncio.current_task()
        writer = client.writer
        if writer is not None and writer is not current and not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        while True:
            try:
                _, _, completion = client.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._reject(completion, RuntimeError("WebSocket client disconnected"))
            client.queue.task_done()
        try:
            await asyncio.wait_for(websocket.close(code=close_code), timeout=self.timeout)
        except Exception:
            pass
        if self.on_disconnect is not None:
            try:
                result = self.on_disconnect(websocket)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

    async def close(self):
        """Disconnect all clients and await all writer/cleanup tasks."""
        self._get_loop()
        self._closed = True
        clients = tuple(self.clients.values())
        if clients:
            await asyncio.gather(*(self._retire(client, 1001) for client in clients))
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
