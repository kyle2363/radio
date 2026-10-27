import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from engine.lifecycle import shutdown_runtime


def test_shutdown_orders_producers_before_services_and_broadcaster():
    calls = []

    async def run():
        async def broadcast():
            try:
                await asyncio.Event().wait()
            finally:
                calls.append("broadcast")
        task = asyncio.create_task(broadcast())
        await asyncio.sleep(0)
        await shutdown_runtime(
            queue=SimpleNamespace(shutdown=lambda: calls.append("queue")),
            player=SimpleNamespace(stop=lambda: calls.append("player")),
            processes=SimpleNamespace(stop_all=lambda: calls.append("processes")),
            broadcaster=task,
        )
        assert task.cancelled()
    asyncio.run(run())
    assert calls == ["queue", "player", "processes", "broadcast"]


def test_cleanup_failure_does_not_leave_player_or_services_running():
    queue = Mock()
    queue.shutdown.side_effect = RuntimeError("worker failed")
    player, processes = Mock(), Mock()
    asyncio.run(shutdown_runtime(queue=queue, player=player, processes=processes))
    player.stop.assert_called_once()
    processes.stop_all.assert_called_once()


def test_player_with_terminal_shutdown_is_closed_instead_of_merely_stopped():
    calls = []
    class TerminalPlayer:
        def stop(self):
            calls.append("stop")
        def shutdown(self):
            calls.append("shutdown")
    asyncio.run(shutdown_runtime(queue=None, player=TerminalPlayer()))
    assert calls == ["shutdown"]
