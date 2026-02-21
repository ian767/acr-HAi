"""Redis-backed cache for real-time robot state."""

from __future__ import annotations

import json
import uuid

import redis.asyncio as redis


class RobotStateCache:
    """Thin wrapper around Redis for low-latency robot state access.

    Each robot's state is stored as a Redis hash at key ``robot:{id}``.
    Planned paths are stored as JSON lists at key ``robot:{id}:path``.
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Position / Status
    # ------------------------------------------------------------------

    async def update_position(
        self,
        robot_id: uuid.UUID,
        row: int,
        col: int,
        heading: float,
    ) -> None:
        """Write the robot's current grid position and heading."""
        key = f"robot:{robot_id}"
        await self._redis.hset(
            key,
            mapping={
                "row": str(row),
                "col": str(col),
                "heading": str(heading),
            },
        )

    async def update_status(self, robot_id: uuid.UUID, status: str) -> None:
        """Write the robot's current status string."""
        key = f"robot:{robot_id}"
        await self._redis.hset(key, "status", status)

    async def get_state(self, robot_id: uuid.UUID) -> dict:
        """Return the full state hash for one robot.

        Returns an empty dict if no data exists.
        """
        key = f"robot:{robot_id}"
        raw = await self._redis.hgetall(key)
        if not raw:
            return {}
        state = {
            "robot_id": str(robot_id),
            "row": int(raw.get("row", 0)),
            "col": int(raw.get("col", 0)),
            "heading": float(raw.get("heading", 0.0)),
            "status": raw.get("status", ""),
            "zone_id": raw.get("zone_id", ""),
        }
        # Include reservation/tote fields if present
        if raw.get("reserved"):
            state["reserved"] = raw.get("reserved") == "1"
        if raw.get("hold_pick_task_id"):
            state["hold_pick_task_id"] = raw.get("hold_pick_task_id")
        if raw.get("hold_at_station"):
            state["hold_at_station"] = raw.get("hold_at_station") == "1"
        return state

    async def get_all_states(self, zone_id: uuid.UUID | None = None) -> list[dict]:
        """Return state dicts for all robots (optionally filtered by zone).

        Uses a ``SCAN`` to discover ``robot:*`` keys.
        """
        states: list[dict] = []
        cursor: int | str = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match="robot:*", count=100
            )
            for key in keys:
                # Skip path keys.
                if isinstance(key, bytes):
                    key = key.decode()
                if key.endswith(":path"):
                    continue
                raw = await self._redis.hgetall(key)
                if not raw:
                    continue
                # Filter by zone if requested.
                if zone_id is not None and raw.get("zone_id") != str(zone_id):
                    continue
                robot_id_str = key.split(":")[-1] if ":" in key else key
                states.append(
                    {
                        "robot_id": robot_id_str,
                        "row": int(raw.get("row", 0)),
                        "col": int(raw.get("col", 0)),
                        "heading": float(raw.get("heading", 0.0)),
                        "status": raw.get("status", ""),
                        "zone_id": raw.get("zone_id", ""),
                    }
                )
            if cursor == 0:
                break
        return states

    # ------------------------------------------------------------------
    # Path
    # ------------------------------------------------------------------

    async def set_path(
        self,
        robot_id: uuid.UUID,
        path: list[tuple[int, int]],
    ) -> None:
        """Store the planned path as a JSON list of ``[row, col]`` pairs."""
        key = f"robot:{robot_id}:path"
        payload = json.dumps([[r, c] for r, c in path])
        await self._redis.set(key, payload)

    async def get_path(self, robot_id: uuid.UUID) -> list[tuple[int, int]]:
        """Retrieve the stored planned path (empty list if none)."""
        key = f"robot:{robot_id}:path"
        raw = await self._redis.get(key)
        if raw is None:
            return []
        data = json.loads(raw)
        return [(int(r), int(c)) for r, c in data]

    # ------------------------------------------------------------------
    # Reservation
    # ------------------------------------------------------------------

    async def update_reservation(
        self,
        robot_id: uuid.UUID,
        reserved: bool = False,
        order_id: uuid.UUID | None = None,
        pick_task_id: uuid.UUID | None = None,
        station_id: uuid.UUID | None = None,
    ) -> None:
        """Write reservation fields to the robot's Redis hash."""
        key = f"robot:{robot_id}"
        mapping = {
            "reserved": "1" if reserved else "0",
            "reservation_order_id": str(order_id) if order_id else "",
            "reservation_pick_task_id": str(pick_task_id) if pick_task_id else "",
            "reservation_station_id": str(station_id) if station_id else "",
        }
        await self._redis.hset(key, mapping=mapping)

    async def clear_reservation(self, robot_id: uuid.UUID) -> None:
        """Clear reservation fields from Redis."""
        key = f"robot:{robot_id}"
        mapping = {
            "reserved": "0",
            "reservation_order_id": "",
            "reservation_pick_task_id": "",
            "reservation_station_id": "",
            "hold_pick_task_id": "",
            "hold_at_station": "0",
        }
        await self._redis.hset(key, mapping=mapping)

    # ------------------------------------------------------------------
    # Tote Possession
    # ------------------------------------------------------------------

    async def update_tote_possession(
        self,
        robot_id: uuid.UUID,
        hold_pick_task_id: uuid.UUID | None = None,
        hold_at_station: bool = False,
    ) -> None:
        """Write tote possession fields to the robot's Redis hash."""
        key = f"robot:{robot_id}"
        mapping = {
            "hold_pick_task_id": str(hold_pick_task_id) if hold_pick_task_id else "",
            "hold_at_station": "1" if hold_at_station else "0",
        }
        await self._redis.hset(key, mapping=mapping)

    async def clear_all(self) -> None:
        """Delete all robot state and path keys from Redis."""
        cursor: int | str = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match="robot:*", count=100
            )
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break
        # Also clear station queue keys
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match="station:*", count=100
            )
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break
