"""Per-tick robot movement simulation."""

from __future__ import annotations

import logging
import uuid

from src.ess.application.fleet_manager import FleetManager
from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import CellType, RobotStatus
from src.ess.infrastructure.redis_cache import RobotStateCache

logger = logging.getLogger(__name__)


class RobotSimulator:
    """Moves robots along their planned paths one step per tick.

    For each robot that has a non-empty path stored in Redis:
        1. Pop the next waypoint.
        2. Attempt to reserve the target cell via :class:`TrafficController`.
        3. On success, update position in Redis and mark the robot MOVING.
        4. On failure (cell occupied), mark the robot WAITING.
        5. When the path is exhausted, mark the robot IDLE and emit arrival events.
    """

    def __init__(
        self,
        fleet_manager: FleetManager,
        traffic_controller: TrafficController,
        redis_cache: RobotStateCache,
        grid: list[list[CellType]] | None = None,
    ) -> None:
        self._fleet = fleet_manager
        self._traffic = traffic_controller
        self._cache = redis_cache
        self._grid = grid

    async def update(self, dt: float) -> None:
        """Called once per simulation tick.

        Parameters
        ----------
        dt:
            Elapsed simulation time in seconds for this tick (used for
            interpolation weighting, currently one discrete step per tick).
        """
        robots = await self._fleet.list_robots()
        position_updates: dict[str, dict] = {}

        for robot in robots:
            path = await self._cache.get_path(robot.id)
            if not path:
                continue

            next_cell = path[0]
            target_row, target_col = next_cell

            # Try to reserve the target cell.
            reserved = self._traffic.reserve_cell(
                target_row, target_col, robot.id
            )

            if not reserved:
                # Cell blocked -- robot waits.
                if robot.status != RobotStatus.WAITING:
                    robot.status = RobotStatus.WAITING
                    await self._cache.update_status(
                        robot.id, RobotStatus.WAITING.value
                    )
                continue

            # Release the robot's previous cell.
            self._traffic.release_cell(robot.grid_row, robot.grid_col, robot.id)

            # Compute heading towards the next cell (0=north, 90=east, etc.).
            heading = self._compute_heading(
                robot.grid_row, robot.grid_col, target_row, target_col
            )

            # Move the robot.
            robot.grid_row = target_row
            robot.grid_col = target_col
            robot.heading = heading
            robot.status = RobotStatus.MOVING

            # Update cache.
            await self._cache.update_position(
                robot.id, target_row, target_col, heading
            )
            await self._cache.update_status(
                robot.id, RobotStatus.MOVING.value
            )

            # Collect for WS broadcast.
            position_updates[str(robot.id)] = {
                "id": str(robot.id),
                "row": target_row,
                "col": target_col,
                "heading": heading,
                "status": RobotStatus.MOVING.value,
            }

            # Consume the waypoint.
            remaining_path = path[1:]
            await self._cache.set_path(robot.id, remaining_path)

            # If path exhausted, robot has arrived.
            if not remaining_path:
                robot.status = RobotStatus.IDLE
                await self._cache.update_status(
                    robot.id, RobotStatus.IDLE.value
                )
                position_updates[str(robot.id)]["status"] = RobotStatus.IDLE.value

                # Emit arrival domain event based on destination cell type.
                await self._emit_arrival_event(robot, target_row, target_col)

        # Broadcast position updates via WebSocket.
        if position_updates:
            from src.shared.websocket_manager import ws_manager
            await ws_manager.broadcast_robot_updates(position_updates)

    # ------------------------------------------------------------------
    # Arrival events
    # ------------------------------------------------------------------

    async def _emit_arrival_event(
        self, robot, target_row: int, target_col: int
    ) -> None:
        """Emit a domain event when a robot arrives at a significant cell."""
        if self._grid is None:
            return

        if target_row >= len(self._grid) or target_col >= len(self._grid[0]):
            return

        cell_type = self._grid[target_row][target_col]
        if cell_type not in (CellType.CANTILEVER, CellType.STATION, CellType.RACK):
            return

        # Look up the EquipmentTask assigned to this robot.
        from src.shared.database import async_session_factory
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from sqlalchemy import select, or_

        async with async_session_factory() as session:
            result = await session.execute(
                select(EquipmentTask).where(
                    or_(
                        EquipmentTask.a42td_robot_id == robot.id,
                        EquipmentTask.k50h_robot_id == robot.id,
                    ),
                    EquipmentTask.state.notin_(["COMPLETED"]),
                ).limit(1)
            )
            eq_task = result.scalar_one_or_none()
            if eq_task is None:
                return

            # Determine the pick_task's source_tote_id
            from src.wes.domain.models import PickTask
            pick_task = await session.get(PickTask, eq_task.pick_task_id)
            if pick_task is None:
                return

            tote_id = pick_task.source_tote_id
            if tote_id is None:
                return

            from src.shared.event_bus import event_bus

            if cell_type == CellType.CANTILEVER:
                if eq_task.type == EquipmentTaskType.RETRIEVE:
                    from src.ess.domain.events import SourceAtCantilever
                    await event_bus.publish(SourceAtCantilever(
                        pick_task_id=eq_task.pick_task_id,
                        tote_id=tote_id,
                    ))
                elif eq_task.type == EquipmentTaskType.RETURN:
                    from src.ess.domain.events import ReturnAtCantilever
                    await event_bus.publish(ReturnAtCantilever(
                        pick_task_id=eq_task.pick_task_id,
                        tote_id=tote_id,
                    ))

            elif cell_type == CellType.STATION:
                from src.ess.domain.events import SourceAtStation
                await event_bus.publish(SourceAtStation(
                    pick_task_id=eq_task.pick_task_id,
                    tote_id=tote_id,
                    station_id=pick_task.station_id,
                ))

            elif cell_type == CellType.RACK:
                from src.ess.domain.events import SourceBackInRack
                if eq_task.target_location_id is not None:
                    await event_bus.publish(SourceBackInRack(
                        pick_task_id=eq_task.pick_task_id,
                        tote_id=tote_id,
                        location_id=eq_task.target_location_id,
                    ))
                elif eq_task.source_location_id is not None:
                    await event_bus.publish(SourceBackInRack(
                        pick_task_id=eq_task.pick_task_id,
                        tote_id=tote_id,
                        location_id=eq_task.source_location_id,
                    ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_heading(
        from_row: int,
        from_col: int,
        to_row: int,
        to_col: int,
    ) -> float:
        """Return a heading in degrees: 0=north, 90=east, 180=south, 270=west."""
        dr = to_row - from_row
        dc = to_col - from_col
        if dr == -1:
            return 0.0    # north
        if dc == 1:
            return 90.0   # east
        if dr == 1:
            return 180.0  # south
        if dc == -1:
            return 270.0  # west
        return 0.0
