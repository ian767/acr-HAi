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

async def get_robot_position(robot_id: Any) -> tuple[int, int] | None:
    """Read current robot position from Redis (simulation state).

    Falls back to None if not cached yet.
    """
    from src.ess.infrastructure.redis_cache import RobotStateCache
    from src.shared.redis import get_redis

    redis_client = await get_redis()
    cache = RobotStateCache(redis_client)
    state = await cache.get_state(robot_id)
    if state and ("row" in state or "col" in state):
        return (state["row"], state["col"])
    return None


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


def find_nearest_rack_edge(
    grid: list[list] | None,
    from_row: int,
    from_col: int,
    avoid_cells: set[tuple[int, int]] | None = None,
) -> tuple[int, int] | None:
    """Return the nearest FLOOR cell on the rack-edge row (cantilever aisle).

    Uses ``simulation_state.rack_edge_row`` to identify the aisle row that
    serves as the cantilever handoff point.  Falls back to the FLOOR cell
    adjacent to the bottom-most RACK row.

    If *avoid_cells* is provided, those cells are skipped.  This lets callers
    distribute multiple robots across different rack-edge positions.
    """
    if grid is None:
        return None
    from src.ess.domain.enums import CellType
    import src.shared.simulation_state as simulation_state

    best: tuple[int, int] | None = None
    best_dist = float("inf")

    if simulation_state.rack_edge_row is not None:
        edge_row = simulation_state.rack_edge_row
        rows = len(grid)
        cols = len(grid[0])
        for c in range(cols):
            if edge_row < rows and grid[edge_row][c] == CellType.FLOOR:
                # Only consider cells adjacent to at least one RACK cell,
                # otherwise _emit_arrival_event won't recognise the arrival.
                has_rack_neighbour = False
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = edge_row + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        if grid[nr][nc] == CellType.RACK:
                            has_rack_neighbour = True
                            break
                if not has_rack_neighbour:
                    continue
                if avoid_cells and (edge_row, c) in avoid_cells:
                    continue
                dist = abs(edge_row - from_row) + abs(c - from_col)
                if dist < best_dist:
                    best_dist = dist
                    best = (edge_row, c)

    # Fallback: FLOOR cell adjacent to bottom-most RACK row.
    if best is None:
        for r in range(len(grid) - 1, -1, -1):
            for c in range(len(grid[0])):
                if grid[r][c] == CellType.FLOOR:
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]):
                            if grid[nr][nc] == CellType.RACK:
                                dist = abs(r - from_row) + abs(c - from_col)
                                if dist < best_dist:
                                    best_dist = dist
                                    best = (r, c)
                                break
            if best is not None:
                break

    return best


# ---------------------------------------------------------------------------
# Tote-in-use check
# ---------------------------------------------------------------------------

async def is_tote_in_use(session: AsyncSession, pick_task_id) -> bool:
    """Check if the source tote for a pick task is being physically processed.

    Returns True if another pick task sharing the same ``source_tote_id``
    has an equipment task that is actively moving the tote (A42TD_MOVING,
    AT_CANTILEVER, K50H_MOVING, or DELIVERED).  PENDING tasks are excluded
    because they haven't started physical movement yet — including them
    would create circular blocking between multiple PENDING tasks for the
    same tote.
    """
    from sqlalchemy import select
    from src.ess.domain.models import EquipmentTask
    from src.ess.domain.enums import EquipmentTaskState
    from src.wes.domain.models import PickTask

    pt = await session.get(PickTask, pick_task_id)
    if pt is None or pt.source_tote_id is None:
        return False

    result = await session.execute(
        select(EquipmentTask.id).where(
            EquipmentTask.pick_task_id.in_(
                select(PickTask.id).where(
                    PickTask.source_tote_id == pt.source_tote_id,
                    PickTask.id != pick_task_id,
                )
            ),
            # Only count tasks that are actively processing the tote.
            # PENDING and COMPLETED are excluded.
            EquipmentTask.state.notin_([
                EquipmentTaskState.COMPLETED,
                EquipmentTaskState.PENDING,
            ]),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


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
