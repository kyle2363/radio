"""Shutdown ordering shared by the application lifespan and desktop endpoint."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def shutdown_runtime(*, queue, player, processes=None, broadcaster=None):
    """Attempt every cleanup even if one component fails; never block the API loop."""
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
    if broadcaster is not None:
        broadcaster.cancel()
        try:
            await broadcaster
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Broadcast worker failed during shutdown")
