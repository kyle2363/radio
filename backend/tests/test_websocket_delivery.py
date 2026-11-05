import asyncio

from websocket_delivery import ClientDelivery


class FakeWebSocket:
    def __init__(self, *, stall=False):
        self.sent = []
        self.closed = []
        self.close_observed = asyncio.Event()
        self.deliveries = asyncio.Queue()
        self.stall = stall
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, message):
        self.started.set()
        if self.stall:
            await self.release.wait()
        self.sent.append(("text", message))
        await self.deliveries.put(("text", message))

    async def send_json(self, message):
        self.started.set()
        if self.stall:
            await self.release.wait()
        self.sent.append(("json", message))
        await self.deliveries.put(("json", message))

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))
        self.close_observed.set()


def test_one_stalled_writer_does_not_block_healthy_client_and_times_out_only_it():
    async def run():
        delivery = ClientDelivery(timeout=0.03)
        slow, healthy = FakeWebSocket(stall=True), FakeWebSocket()
        delivery.add(slow)
        delivery.add(healthy)
        delivery.publish({"event": "first"})
        await asyncio.wait_for(healthy.started.wait(), 0.2)
        await asyncio.wait_for(slow.started.wait(), 0.2)
        delivery.publish({"event": "second"})
        await asyncio.sleep(0.06)
        assert healthy.sent == [("json", {"event": "first"}), ("json", {"event": "second"})]
        assert slow not in delivery.clients
        assert (1013, "") in slow.closed
        await delivery.close()

    asyncio.run(run())


def test_direct_text_json_sends_are_serialized_with_published_events_and_cleanup():
    async def run():
        delivery = ClientDelivery()
        ws = FakeWebSocket()
        delivery.add(ws)
        delivery.publish({"event": "before"})
        await delivery.send_text(ws, "pong")
        await delivery.send_json(ws, {"event": "after"})
        assert ws.sent == [
            ("json", {"event": "before"}),
            ("text", "pong"),
            ("json", {"event": "after"}),
        ]
        await delivery.close()
        assert not delivery.clients
        assert not delivery.tasks

    asyncio.run(run())


def test_full_client_queue_evicts_slow_client_without_affecting_another():
    async def run():
        delivery = ClientDelivery(max_pending=1, timeout=1)
        slow, healthy = FakeWebSocket(stall=True), FakeWebSocket()
        delivery.add(slow)
        delivery.add(healthy)
        delivery.publish({"n": 1})
        await asyncio.wait_for(slow.started.wait(), 0.2)
        await asyncio.wait_for(healthy.deliveries.get(), 0.2)
        delivery.publish({"n": 2})
        await asyncio.wait_for(healthy.deliveries.get(), 0.2)
        delivery.publish({"n": 3})
        await asyncio.wait_for(healthy.deliveries.get(), 0.2)
        assert slow not in delivery.clients
        assert healthy in delivery.clients
        await asyncio.wait_for(slow.close_observed.wait(), 0.2)
        assert (1013, "") in slow.closed
        await delivery.close()

    asyncio.run(run())


def test_overflow_does_not_create_unbounded_cleanup_tasks():
    async def run():
        delivery = ClientDelivery(max_pending=1)
        slow = FakeWebSocket(stall=True)
        delivery.add(slow)
        for index in range(1000):
            delivery.publish({'n': index})
        assert len(delivery.tasks) == 2  # One writer and one retirement.
        await delivery.close()
        assert not delivery.tasks
    asyncio.run(run())


def test_close_releases_both_active_and_queued_direct_senders():
    async def run():
        delivery = ClientDelivery()
        slow = FakeWebSocket(stall=True)
        delivery.add(slow)
        first = asyncio.create_task(delivery.send_text(slow, 'first'))
        await slow.started.wait()
        second = asyncio.create_task(delivery.send_json(slow, {'event': 'queued'}))
        await asyncio.sleep(0)
        await delivery.close()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
        assert not delivery.tasks
    asyncio.run(run())
