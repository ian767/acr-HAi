"""Fleet management: robot CRUD, assignment, and nearest-idle lookup."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.domain.enums import RobotStatus, RobotType
from src.ess.domain.models import Robot


class FleetManager:
    """Manages robot lifecycle and assignment within the ESS."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register_robot(
        self,
        name: str,
        type: RobotType,
        zone_id: uuid.UUID,
        row: int,
        col: int,
    ) -> Robot:
        """Create and persist a new robot in the given zone."""
        robot = Robot(
            name=name,
            type=type,
            zone_id=zone_id,
            grid_row=row,
            grid_col=col,
            status=RobotStatus.IDLE,
        )
        self._session.add(robot)
        await self._session.flush()
        return robot

    async def get_robot(self, robot_id: uuid.UUID) -> Robot:
        """Fetch a robot by its primary key, raising if not found."""
        result = await self._session.get(Robot, robot_id)
        if result is None:
            raise ValueError(f"Robot {robot_id} not found")
        return result

    async def list_robots(
        self,
        zone_id: uuid.UUID | None = None,
        status: RobotStatus | None = None,
    ) -> list[Robot]:
        """Return robots, optionally filtered by zone and/or status."""
        stmt = select(Robot)
        if zone_id is not None:
            stmt = stmt.where(Robot.zone_id == zone_id)
        if status is not None:
            stmt = stmt.where(Robot.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def assign_robot(self, robot_id: uuid.UUID, task_id: uuid.UUID) -> Robot:
        """Mark a robot as ASSIGNED to the given task."""
        robot = await self.get_robot(robot_id)
        robot.status = RobotStatus.ASSIGNED
        robot.current_task_id = task_id
        await self._session.flush()
        return robot

    async def release_robot(
        self,
        robot_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        position: tuple[int, int] | None = None,
    ) -> Robot:
        """Release a robot back to IDLE, clearing its task.

        If *task_id* is provided the release is **conditional**: the robot is
        only released when its ``current_task_id`` still matches *task_id*.
        This prevents accidentally resetting a robot that has already been
        reassigned to a different task.

        If *position* is provided, the robot's DB grid coordinates are updated
        so that future ``find_nearest_idle`` calls use accurate positions.
        """
        robot = await self.get_robot(robot_id)
        if task_id is not None and robot.current_task_id != task_id:
            return robot  # Robot already reassigned — skip release.
        robot.status = RobotStatus.IDLE
        robot.current_task_id = None
        # Clear reservation fields so the robot can be reassigned.
        robot.reserved = False
        robot.reservation_order_id = None
        robot.reservation_pick_task_id = None
        robot.reservation_station_id = None
        robot.hold_pick_task_id = None
        robot.hold_at_station = False
        if position is not None:
            robot.grid_row, robot.grid_col = position
        await self._session.flush()
        return robot

    async def find_nearest_idle(
        self,
        zone_id: uuid.UUID,
        robot_type: RobotType,
        target_row: int,
        target_col: int,
    ) -> Robot | None:
        """Find the nearest IDLE robot of the given type using Manhattan distance.

        Returns ``None`` when no idle robot of that type exists in the zone.
        """
        robots = await self.list_robots(zone_id=zone_id, status=RobotStatus.IDLE)
        candidates = [r for r in robots if r.type == robot_type and not r.reserved]
        if not candidates:
            return None

        def manhattan(r: Robot) -> int:
            return abs(r.grid_row - target_row) + abs(r.grid_col - target_col)

        return min(candidates, key=manhattan)
