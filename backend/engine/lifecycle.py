"""Shutdown ordering shared by the application lifespan and desktop endpoint."""
import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)


async def shutdown_runtime(*, queue, player, processes=None, broadcaster=None,
                           startup_stop=None, startup_threads=()):
    """Attempt every cleanup even if one component fails; never block the API loop."""
    if startup_stop is not None:
        startup_stop.set()
    for name, component, method in (
        ("queue", queue, "shutdown"),
        ("player", player, "shutdown" if callable(getattr(type(player), "shutdown", None)) else "stop"),
        ("processes", processes, "stop_all"),
    ):
        if component is not None:
            try:
                await asyncio.to_thread(getattr(component, method))
            except Exception:
                logger.exception("Runtime cleanup failed: component=%s", name)
    if startup_threads:
        def join_startup():
            deadline = time.monotonic() + 2.0
            for worker in startup_threads:
                if worker is not threading.current_thread():
                    worker.join(max(0, deadline - time.monotonic()))
            remaining = [worker.name for worker in startup_threads if worker.is_alive()]
            if remaining:
                logger.warning("Startup tasks still finishing: %s", remaining)
        await asyncio.to_thread(join_startup)
    if broadcaster is not None:
        broadcaster.cancel()
        try:
            await broadcaster
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Broadcast worker failed during shutdown")
