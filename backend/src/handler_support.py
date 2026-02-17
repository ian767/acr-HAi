"""Shared helpers for domain event handlers."""

from __future__ import annotations

import functools
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

@asynccontextmanager
async def handler_session() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session for use inside event handlers."""
    from src.shared.database import async_session_factory

    async with async_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Service bundle
# ---------------------------------------------------------------------------

class HandlerServices:
    """Convenience wrapper: creates FleetManager + PathPlanner + TaskExecutor."""

    def __init__(self, session: AsyncSession) -> None:
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        import src.shared.simulation_state as simulation_state

        self.fm = FleetManager(session)
        self.planner = PathPlanner(simulation_state.grid or [])
        self.traffic = simulation_state.traffic
        self.executor = TaskExecutor(session, self.fm, self.planner, self.traffic)


# ---------------------------------------------------------------------------
# Path planning + Redis storage
# ---------------------------------------------------------------------------

async def plan_and_store_path(
    services: HandlerServices,
    robot_id: Any,
    start: tuple[int, int],
    end: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """Compute A* path and store in Redis. Returns the path (excluding start) or None."""
    path = services.planner.find_path(start, end)
    if path:
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis

        redis_client = await get_redis()
        cache = RobotStateCache(redis_client)
        await cache.set_path(robot_id, path[1:])  # skip current position
        return path[1:]
    return None


# ---------------------------------------------------------------------------
# WebSocket broadcast
# ---------------------------------------------------------------------------

async def ws_broadcast(message_type: str, payload: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    from src.shared.websocket_manager import ws_manager

    await ws_manager.broadcast(message_type, payload)


# ---------------------------------------------------------------------------
# Safe handler decorator
# ---------------------------------------------------------------------------

def safe_handler(func):
    """Wrap an event handler with try-except logging. Re-raises the exception."""

    @functools.wraps(func)
    async def wrapper(event: Any) -> None:
        try:
            await func(event)
        except Exception:
            logger.exception(
                "Error in handler %s for event %s",
                func.__name__,
                type(event).__name__,
            )
            raise

    return wrapper
