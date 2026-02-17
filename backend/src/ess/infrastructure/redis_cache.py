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
        return {
            "robot_id": str(robot_id),
            "row": int(raw.get("row", 0)),
            "col": int(raw.get("col", 0)),
            "heading": float(raw.get("heading", 0.0)),
            "status": raw.get("status", ""),
            "zone_id": raw.get("zone_id", ""),
        }

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
