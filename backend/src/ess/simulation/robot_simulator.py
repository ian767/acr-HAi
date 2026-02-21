"""Per-tick robot movement simulation."""

from __future__ import annotations

import logging
import random
import uuid

from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import CellType, RobotStatus
from src.ess.infrastructure.redis_cache import RobotStateCache

logger = logging.getLogger(__name__)

_REROUTE_WAIT_THRESHOLD = 3
_HEATMAP_BROADCAST_INTERVAL = 10


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
        path_planner: PathPlanner | None = None,
    ) -> None:
        self._fleet = fleet_manager
        self._traffic = traffic_controller
        self._cache = redis_cache
        self._grid = grid
        self._planner = path_planner
        self._wait_counts: dict[uuid.UUID, int] = {}
        self._tick_counter: int = 0
        self._floor_cells: list[tuple[int, int]] | None = None
        self._robots: list | None = None  # Cached robot list (loaded once)
        # Per-robot movement cooldown accumulator (seconds).
        self._move_cooldowns: dict[uuid.UUID, float] = {}
        # Cached robot_id → task_type ("RETRIEVE" | "RETURN" | None).
        self._robot_task_types: dict[str, str | None] = {}

    async def update(self, dt: float) -> None:
        """Called once per simulation tick.

        Parameters
        ----------
        dt:
            Elapsed simulation time in seconds for this tick (used for
            interpolation weighting, currently one discrete step per tick).
        """
        self._tick_counter += 1
        # Load robots once and cache; the DB session from the HTTP request
        # is closed after the endpoint returns, so re-querying would reset
        # in-memory position updates.
        if self._robots is None:
            self._robots = await self._fleet.list_robots()
            # Reserve each robot's starting cell so the traffic controller
            # correctly prevents collisions from tick one.
            for r in self._robots:
                self._traffic.reserve_cell(r.grid_row, r.grid_col, r.id)
        robots = self._robots
        robot_map = {r.id: r for r in robots}
        position_updates: dict[str, dict] = {}

        import src.shared.simulation_state as simulation_state
        speed_cfg = simulation_state.robot_speed

        for robot in robots:
            # Skip robots waiting at station UNLESS they now have a path
            # (meaning the return flow has started and released them).
            if robot.status == RobotStatus.WAITING_FOR_STATION:
                path = await self._cache.get_path(robot.id)
                if path:
                    # Return flow started — sync in-memory status to ASSIGNED
                    robot.status = RobotStatus.MOVING
                    robot.hold_at_station = False  # type: ignore[attr-defined]
                    logger.info(
                        "K50H %s released from WAITING_FOR_STATION (return path found)",
                        robot.id,
                    )
                else:
                    continue

            path = await self._cache.get_path(robot.id)
            if not path:
                robot._next_cell = None  # type: ignore[attr-defined]
                continue

            # Movement cooldown: accumulate dt, only move when threshold met.
            robot_type = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
            move_interval = speed_cfg.get(robot_type, 0.3)
            self._move_cooldowns[robot.id] = self._move_cooldowns.get(robot.id, 0.0) + dt
            if self._move_cooldowns[robot.id] < move_interval:
                # Not enough time accumulated — robot stays put this tick.
                # Still set _next_cell for deadlock detection.
                robot._next_cell = path[0]  # type: ignore[attr-defined]
                continue
            # Consume one move's worth of cooldown.
            self._move_cooldowns[robot.id] -= move_interval

            next_cell = path[0]
            target_row, target_col = next_cell

            # Set _next_cell for deadlock detection.
            robot._next_cell = next_cell  # type: ignore[attr-defined]

            # Try to reserve the target cell.
            reserved = self._traffic.reserve_cell(
                target_row, target_col, robot.id
            )

            if not reserved:
                # Cell blocked -- robot waits.
                self._wait_counts[robot.id] = self._wait_counts.get(robot.id, 0) + 1

                if robot.status != RobotStatus.WAITING:
                    robot.status = RobotStatus.WAITING
                    await self._cache.update_status(
                        robot.id, RobotStatus.WAITING.value
                    )
                    # Broadcast WAITING transition so frontend knows.
                    remaining = [[r, c] for r, c in path]
                    position_updates[str(robot.id)] = {
                        "id": str(robot.id),
                        "name": robot.name,
                        "type": robot.type.value if hasattr(robot.type, "value") else str(robot.type),
                        "row": robot.grid_row,
                        "col": robot.grid_col,
                        "heading": robot.heading,
                        "status": RobotStatus.WAITING.value,
                        "path": remaining,
                        "task_type": self._robot_task_types.get(str(robot.id)),
                    }

                # Congestion-aware reroute after threshold.
                if (
                    self._wait_counts[robot.id] >= _REROUTE_WAIT_THRESHOLD
                    and self._planner is not None
                    and self._grid is not None
                ):
                    # If the blocker is IDLE (no path), nudge it out of the way.
                    blocker_id = self._traffic.occupied_cells.get(next_cell)
                    if blocker_id is not None and blocker_id != robot.id:
                        blocker_robot = robot_map.get(blocker_id)
                        if blocker_robot is not None:
                            blocker_path = await self._cache.get_path(blocker_id)
                            if not blocker_path:
                                await self._park_one_step(
                                    blocker_robot,
                                    blocker_robot.grid_row,
                                    blocker_robot.grid_col,
                                )
                                logger.info(
                                    "Nudged idle blocker %s at (%d,%d) out of way for %s",
                                    blocker_id, blocker_robot.grid_row,
                                    blocker_robot.grid_col, robot.id,
                                )
                                # Let the blocker move on the next tick before
                                # we try to reroute — avoid overwriting its
                                # park path on every tick.
                                self._wait_counts[robot.id] = 0
                                continue

                    congestion = self._traffic.get_congestion_map()
                    for cell, occupant in self._traffic.occupied_cells.items():
                        if occupant != robot.id:
                            congestion[cell] = congestion.get(cell, 0.0) + 50.0
                    planner = PathPlanner(self._grid, congestion=congestion)
                    goal = path[-1]
                    new_path = planner.find_path(
                        (robot.grid_row, robot.grid_col), goal,
                    )
                    if new_path and len(new_path) > 1:
                        await self._cache.set_path(robot.id, new_path[1:])
                    self._wait_counts[robot.id] = 0
                continue

            # Successfully reserved — clear wait counter.
            self._wait_counts.pop(robot.id, None)

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

            # Consume the waypoint.
            remaining_path = path[1:]
            await self._cache.set_path(robot.id, remaining_path)

            # Collect for WS broadcast (include remaining path for frontend).
            remaining_serialized = [[r, c] for r, c in remaining_path]
            position_updates[str(robot.id)] = {
                "id": str(robot.id),
                "name": robot.name,
                "type": robot.type.value if hasattr(robot.type, "value") else str(robot.type),
                "row": target_row,
                "col": target_col,
                "heading": heading,
                "status": RobotStatus.MOVING.value,
                "path": remaining_serialized,
                "task_type": self._robot_task_types.get(str(robot.id)),
            }

            # Broadcast robot.move_started event
            from src.shared.websocket_manager import ws_manager
            await ws_manager.broadcast("robot.move_started", {
                "type": "robot.move_started",
                "robotId": str(robot.id),
                "from": [robot.grid_row, robot.grid_col],
                "to": [target_row, target_col],
            })

            # If path exhausted, robot has arrived.
            if not remaining_path:
                robot.status = RobotStatus.IDLE
                await self._cache.update_status(
                    robot.id, RobotStatus.IDLE.value
                )
                position_updates[str(robot.id)]["status"] = RobotStatus.IDLE.value

                # Broadcast robot.target_reached
                await ws_manager.broadcast("robot.target_reached", {
                    "type": "robot.target_reached",
                    "robotId": str(robot.id),
                    "position": [target_row, target_col],
                })

                # Emit arrival domain event based on destination cell type.
                await self._emit_arrival_event(robot, target_row, target_col)

                # If the robot is still idle at a high-traffic cell, park it
                # on a nearby FLOOR cell so it doesn't block other robots.
                # Skip if WAITING_FOR_STATION (holding tote at station).
                new_path = await self._cache.get_path(robot.id)
                if not new_path and self._grid is not None and robot.status != RobotStatus.WAITING_FOR_STATION:
                    should_park = False
                    cell = self._grid[target_row][target_col]
                    if cell == CellType.STATION:
                        should_park = True
                    # Also park away from rack_edge_row to avoid
                    # blocking other robots heading to cantilever.
                    import src.shared.simulation_state as _park_sim
                    if _park_sim.rack_edge_row is not None and target_row == _park_sim.rack_edge_row:
                        should_park = True
                    if should_park:
                        await self._park_one_step(robot, target_row, target_col)

        # ------------------------------------------------------------------
        # Auto-dispatch: assign random FLOOR destinations to IDLE robots
        # (skipped when WES-driven mode is active — robots receive paths
        # exclusively from the event handler chain)
        # ------------------------------------------------------------------
        if (
            simulation_state.auto_dispatch
            and not simulation_state.wes_driven
            and not simulation_state.interactive_mode
            and self._grid
        ):
            for robot in robots:
                path = await self._cache.get_path(robot.id)
                if path or robot.status != RobotStatus.IDLE:
                    continue
                goal = self._random_floor_cell()
                if goal and goal != (robot.grid_row, robot.grid_col):
                    planner = PathPlanner(self._grid)
                    new_path = planner.find_path(
                        (robot.grid_row, robot.grid_col), goal,
                    )
                    if new_path and len(new_path) > 1:
                        await self._cache.set_path(robot.id, new_path[1:])
                        logger.debug(
                            "Auto-dispatch robot %s -> (%d, %d)",
                            robot.id, goal[0], goal[1],
                        )

        # ------------------------------------------------------------------
        # Deadlock detection and resolution
        # ------------------------------------------------------------------
        deadlocked_ids = self._traffic.detect_deadlock(robots)
        if deadlocked_ids:
            logger.warning("Deadlock detected among robots: %s", deadlocked_ids)
            await self._resolve_deadlock(robots, deadlocked_ids)

        # Broadcast position updates via WebSocket.
        if position_updates:
            from src.shared.websocket_manager import ws_manager
            await ws_manager.broadcast_robot_updates(position_updates)

        # ------------------------------------------------------------------
        # Periodic heatmap + task-type refresh
        # ------------------------------------------------------------------
        if self._tick_counter % _HEATMAP_BROADCAST_INTERVAL == 0:
            await self._refresh_task_types()
            await self._broadcast_heatmap()

    # ------------------------------------------------------------------
    # Auto-dispatch helpers
    # ------------------------------------------------------------------

    def _random_floor_cell(self) -> tuple[int, int] | None:
        """Return a random FLOOR cell from the grid (cached)."""
        if self._floor_cells is None and self._grid is not None:
            self._floor_cells = [
                (r, c)
                for r in range(len(self._grid))
                for c in range(len(self._grid[0]))
                if self._grid[r][c] == CellType.FLOOR
            ]
        if not self._floor_cells:
            return None
        return random.choice(self._floor_cells)

    # ------------------------------------------------------------------
    # Deadlock resolution
    # ------------------------------------------------------------------

    async def _resolve_deadlock(
        self, robots: list, deadlocked_ids: list[uuid.UUID],
    ) -> None:
        """Clear and replan the path of the robot with the shortest remaining path.

        If any deadlocked robot is IDLE (no path), park it away first so it
        stops blocking the corridor.
        """
        robot_map = {r.id: r for r in robots}

        # First pass: move any IDLE (no-path) robots out of the way.
        for rid in deadlocked_ids:
            robot = robot_map.get(rid)
            if robot is None:
                continue
            path = await self._cache.get_path(rid)
            if not path and self._grid is not None:
                # IDLE robot blocking — park it on a nearby FLOOR cell.
                await self._park_to_floor(robot, robot.grid_row, robot.grid_col)
                parked_path = await self._cache.get_path(rid)
                if parked_path:
                    logger.info(
                        "Deadlock resolved: parked idle robot %s away from (%d,%d)",
                        rid, robot.grid_row, robot.grid_col,
                    )
                    return  # Resolved by moving the idle robot.

        # Second pass: reroute the robot with the shortest remaining path.
        best_robot = None
        best_path_len = float("inf")
        for rid in deadlocked_ids:
            robot = robot_map.get(rid)
            if robot is None:
                continue
            path = await self._cache.get_path(rid)
            if path and len(path) < best_path_len:
                best_path_len = len(path)
                best_robot = robot

        if best_robot is None:
            return

        path = await self._cache.get_path(best_robot.id)
        if not path:
            return

        goal = path[-1]
        start = (best_robot.grid_row, best_robot.grid_col)
        # Clear current path so the robot yields its next cell.
        await self._cache.set_path(best_robot.id, [])
        self._wait_counts.pop(best_robot.id, None)

        # Attempt reroute: penalise currently occupied cells heavily so the
        # planner steers around the deadlocked cluster.
        if self._grid is not None:
            congestion = self._traffic.get_congestion_map()
            for cell, occupant in self._traffic.occupied_cells.items():
                if occupant != best_robot.id:
                    congestion[cell] = congestion.get(cell, 0.0) + 50.0
            planner = PathPlanner(self._grid, congestion=congestion)
            new_path = planner.find_path(start, goal)
            if new_path and len(new_path) > 1:
                await self._cache.set_path(best_robot.id, new_path[1:])
                logger.info(
                    "Deadlock resolved: rerouted robot %s", best_robot.id,
                )
            else:
                # Reroute failed — mark robot IDLE so auto-dispatch can reassign.
                best_robot.status = RobotStatus.IDLE
                await self._cache.update_status(
                    best_robot.id, RobotStatus.IDLE.value
                )

    # ------------------------------------------------------------------
    # Task-type cache
    # ------------------------------------------------------------------

    async def _refresh_task_types(self) -> None:
        """Refresh the robot_id → task_type mapping from active EquipmentTasks."""
        from src.shared.database import async_session_factory
        from src.ess.domain.models import EquipmentTask
        from sqlalchemy import select

        mapping: dict[str, str | None] = {}
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.state.notin_(["COMPLETED"])
                    )
                )
                for task in result.scalars():
                    task_type = task.type.value if hasattr(task.type, "value") else str(task.type)
                    if task.a42td_robot_id:
                        mapping[str(task.a42td_robot_id)] = task_type
                    if task.k50h_robot_id:
                        mapping[str(task.k50h_robot_id)] = task_type
        except Exception:
            return  # Keep previous mapping on error
        self._robot_task_types = mapping

    # ------------------------------------------------------------------
    # Heatmap broadcast
    # ------------------------------------------------------------------

    async def _broadcast_heatmap(self) -> None:
        """Send the congestion map to all connected WS clients."""
        congestion = self._traffic.get_congestion_map()
        if not congestion:
            return
        serialized = {
            f"{r},{c}": v for (r, c), v in congestion.items()
        }
        from src.shared.websocket_manager import ws_manager
        await ws_manager.broadcast("heatmap.updated", {"cells": serialized})

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

        # Since RACK is impassable, robots land on FLOOR cells adjacent to
        # RACK.  Detect this so the rest of the handler treats it like a
        # rack arrival.  Also treat any cell on rack_edge_row as
        # rack-adjacent (it's the cantilever handoff row even if the
        # specific column isn't directly touching a RACK cell).
        import src.shared.simulation_state as _sim
        is_rack_adjacent = False
        if cell_type == CellType.FLOOR:
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = target_row + dr, target_col + dc
                if 0 <= nr < len(self._grid) and 0 <= nc < len(self._grid[0]):
                    if self._grid[nr][nc] == CellType.RACK:
                        is_rack_adjacent = True
                        break
            # Any cell on rack_edge_row counts as rack-adjacent.
            if not is_rack_adjacent and _sim.rack_edge_row is not None:
                if target_row == _sim.rack_edge_row:
                    is_rack_adjacent = True

        if cell_type not in (CellType.STATION,) and not is_rack_adjacent:
            return

        # Look up the EquipmentTask assigned to this robot.
        # IMPORTANT: collect all data inside the session, then close it
        # BEFORE publishing events.  Event handlers open their own write
        # sessions, and SQLite doesn't support concurrent transactions —
        # keeping the read session open would cause "database is locked".
        from src.shared.database import async_session_factory
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from sqlalchemy import select, or_

        eq_pick_task_id = None
        eq_type = None
        eq_a42td_robot_id = None
        eq_target_location_id = None
        eq_source_location_id = None
        tote_id = None
        pick_task_state = None
        pick_task_station_id = None

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

            eq_pick_task_id = eq_task.pick_task_id
            eq_type = eq_task.type
            eq_a42td_robot_id = eq_task.a42td_robot_id
            eq_target_location_id = eq_task.target_location_id
            eq_source_location_id = eq_task.source_location_id

            from src.wes.domain.models import PickTask
            pick_task = await session.get(PickTask, eq_task.pick_task_id)
            if pick_task is None:
                return

            tote_id = pick_task.source_tote_id
            if tote_id is None:
                return

            pick_task_state = pick_task.state
            pick_task_station_id = pick_task.station_id
        # --- session closed ---

        from src.shared.event_bus import event_bus
        import src.shared.simulation_state as sim_state

        if cell_type == CellType.STATION:
            from src.ess.domain.events import SourcePicked, SourceAtStation
            from src.wes.domain.enums import PickTaskState

            if pick_task_state == PickTaskState.SOURCE_AT_CANTILEVER:
                await event_bus.publish(SourcePicked(
                    pick_task_id=eq_pick_task_id,
                    tote_id=tote_id,
                    robot_id=robot.id,
                ))

            # Set robot to WAITING_FOR_STATION (holds tote, stays at station)
            robot.status = RobotStatus.WAITING_FOR_STATION
            await self._cache.update_status(
                robot.id, RobotStatus.WAITING_FOR_STATION.value
            )

            await event_bus.publish(SourceAtStation(
                pick_task_id=eq_pick_task_id,
                tote_id=tote_id,
                station_id=pick_task_station_id,
            ))

        elif cell_type == CellType.RACK or is_rack_adjacent:
            is_edge = (
                sim_state.rack_edge_row is not None
                and target_row == sim_state.rack_edge_row
            )

            if eq_type == EquipmentTaskType.RETRIEVE:
                if is_edge:
                    from src.ess.domain.events import SourceAtCantilever
                    await event_bus.publish(SourceAtCantilever(
                        pick_task_id=eq_pick_task_id,
                        tote_id=tote_id,
                    ))
                else:
                    await self._dispatch_to_rack_edge(robot, target_row, target_col)
            elif eq_type == EquipmentTaskType.RETURN:
                if robot.id == eq_a42td_robot_id:
                    from src.ess.domain.events import SourceBackInRack
                    loc_id = eq_target_location_id or eq_source_location_id
                    if loc_id is not None:
                        await event_bus.publish(SourceBackInRack(
                            pick_task_id=eq_pick_task_id,
                            tote_id=tote_id,
                            location_id=loc_id,
                        ))
                elif is_edge or is_rack_adjacent:
                    # K50H arrived at cantilever area. Accept both exact
                    # rack_edge_row and any rack-adjacent FLOOR cell so the
                    # RETURN flow doesn't silently stall if the robot lands
                    # one row off due to path-planning variation.
                    from src.ess.domain.events import ReturnAtCantilever
                    await event_bus.publish(ReturnAtCantilever(
                        pick_task_id=eq_pick_task_id,
                        tote_id=tote_id,
                    ))

    async def _dispatch_to_rack_edge(
        self, robot, from_row: int, from_col: int
    ) -> None:
        """Plan A42TD path from deep rack to nearest rack-edge cell."""
        if self._grid is None:
            return

        import src.shared.simulation_state as sim_state

        # Find the nearest FLOOR cell on the rack_edge_row (cantilever aisle).
        best: tuple[int, int] | None = None
        best_dist = float("inf")

        if sim_state.rack_edge_row is not None:
            edge_row = sim_state.rack_edge_row
            for c in range(len(self._grid[0])):
                if edge_row < len(self._grid) and self._grid[edge_row][c] == CellType.FLOOR:
                    dist = abs(edge_row - from_row) + abs(c - from_col)
                    if dist < best_dist:
                        best_dist = dist
                        best = (edge_row, c)

        # Fallback: find the FLOOR cell adjacent to the bottom-most RACK row.
        if best is None:
            for r in range(len(self._grid) - 1, -1, -1):
                for c in range(len(self._grid[0])):
                    if self._grid[r][c] == CellType.FLOOR:
                        # Check if adjacent to a RACK cell
                        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                            nr, nc = r + dr, c + dc
                            if 0 <= nr < len(self._grid) and 0 <= nc < len(self._grid[0]):
                                if self._grid[nr][nc] == CellType.RACK:
                                    dist = abs(r - from_row) + abs(c - from_col)
                                    if dist < best_dist:
                                        best_dist = dist
                                        best = (r, c)
                                    break
                if best is not None:
                    break

        if best is None:
            logger.warning("No rack-edge cell found for A42TD dispatch")
            return

        from src.ess.application.path_planner import PathPlanner
        planner = PathPlanner(self._grid)
        path = planner.find_path((from_row, from_col), best)
        if path and len(path) > 1:
            await self._cache.set_path(robot.id, path[1:])
            logger.info(
                "A42TD %s dispatched from rack (%d,%d) to rack-edge (%d,%d)",
                robot.id, from_row, from_col, best[0], best[1],
            )

    # ------------------------------------------------------------------
    # Idle parking
    # ------------------------------------------------------------------

    async def _park_one_step(
        self, robot, from_row: int, from_col: int,
    ) -> None:
        """Move an idle robot exactly ONE cell to a free adjacent FLOOR cell.

        Unlike ``_park_to_floor`` this never plans a multi-step path, so it
        cannot create new corridor congestion or deadlocks.
        """
        if self._grid is None:
            return
        import src.shared.simulation_state as _park_sim
        rows = len(self._grid)
        cols = len(self._grid[0])
        # Try the 4 cardinal neighbours; prefer cells away from rack_edge_row.
        candidates: list[tuple[int, int]] = []
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = from_row + dr, from_col + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if self._grid[nr][nc] != CellType.FLOOR:
                    continue
                # Skip rack_edge_row (high-traffic)
                if _park_sim.rack_edge_row is not None and nr == _park_sim.rack_edge_row:
                    continue
                # Skip occupied cells
                if (nr, nc) in self._traffic.occupied_cells:
                    continue
                candidates.append((nr, nc))
        if candidates:
            target = candidates[0]
            await self._cache.set_path(robot.id, [target])

    async def _park_to_floor(
        self, robot, from_row: int, from_col: int,
    ) -> None:
        """Move an idle robot off a key cell to the nearest FLOOR cell."""
        if self._grid is None:
            return
        import src.shared.simulation_state as _park_sim
        rows = len(self._grid)
        cols = len(self._grid[0])
        # Expand outward by Manhattan distance to find closest FLOOR.
        # Avoid rack_edge_row (high-traffic corridor) and STATION cells.
        for dist in range(1, max(rows, cols)):
            for dr in range(-dist, dist + 1):
                dc_abs = dist - abs(dr)
                for dc in ([-dc_abs, dc_abs] if dc_abs else [0]):
                    nr, nc = from_row + dr, from_col + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        if self._grid[nr][nc] != CellType.FLOOR:
                            continue
                        # Skip rack_edge_row (high-traffic)
                        if _park_sim.rack_edge_row is not None and nr == _park_sim.rack_edge_row:
                            continue
                        planner = PathPlanner(self._grid)
                        path = planner.find_path(
                            (from_row, from_col), (nr, nc),
                        )
                        if path and len(path) > 1:
                            await self._cache.set_path(robot.id, path[1:])
                            return

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
