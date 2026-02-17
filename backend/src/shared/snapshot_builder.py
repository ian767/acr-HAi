"""Build a full state snapshot for WebSocket initial connection."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from src.shared.database import async_session_factory


async def build_snapshot() -> dict[str, Any]:
    """Query DB and return a snapshot matching the frontend SnapshotPayload shape.

    Returns
    -------
    dict with keys: robots, stations, pick_tasks, orders, alarms
    """
    async with async_session_factory() as session:
        from src.ess.domain.models import Robot
        from src.wes.domain.models import Order, PickTask, Station

        # Robots — include cached path data for immediate frontend display.
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis

        result = await session.execute(select(Robot))
        robots_raw = result.scalars().all()

        redis_client = await get_redis()
        cache = RobotStateCache(redis_client)
        robots: dict[str, Any] = {}
        for r in robots_raw:
            path = await cache.get_path(r.id)
            robots[str(r.id)] = {
                "id": str(r.id),
                "name": r.name,
                "type": r.type.value,
                "row": r.grid_row,
                "col": r.grid_col,
                "heading": r.heading,
                "status": r.status.value,
                "path": [[row, col] for row, col in path] if path else [],
            }

        # Stations
        result = await session.execute(select(Station))
        stations_raw = result.scalars().all()
        stations = [
            {
                "id": str(s.id),
                "name": s.name,
                "zone_id": str(s.zone_id),
                "grid_row": s.grid_row,
                "grid_col": s.grid_col,
                "is_online": s.is_online,
                "status": s.status.value,
            }
            for s in stations_raw
        ]

        # Pick Tasks
        result = await session.execute(select(PickTask))
        tasks_raw = result.scalars().all()
        pick_tasks = [
            {
                "id": str(t.id),
                "order_id": str(t.order_id),
                "station_id": str(t.station_id),
                "sku": t.sku,
                "qty_to_pick": t.qty_to_pick,
                "qty_picked": t.qty_picked,
                "state": t.state.value,
            }
            for t in tasks_raw
        ]

        # Orders
        result = await session.execute(select(Order))
        orders_raw = result.scalars().all()
        orders = [
            {
                "id": str(o.id),
                "external_id": o.external_id,
                "sku": o.sku,
                "quantity": o.quantity,
                "status": o.status.value,
                "station_id": str(o.station_id) if o.station_id else None,
            }
            for o in orders_raw
        ]

    return {
        "robots": robots,
        "stations": stations,
        "pick_tasks": pick_tasks,
        "orders": orders,
        "alarms": [],
    }
