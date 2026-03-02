"""Per-tick robot movement simulation."""

from __future__ import annotations

import logging
import random
import uuid

from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import CellType, RobotStatus, RobotType
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
        preloaded_robots: list | None = None,
    ) -> None:
        self._fleet = fleet_manager
        self._traffic = traffic_controller
        self._cache = redis_cache
        self._grid = grid
        self._planner = path_planner
        self._wait_counts: dict[uuid.UUID, int] = {}
        self._tick_counter: int = 0
        self._floor_cells: list[tuple[int, int]] | None = None
        self._robots: list | None = preloaded_robots  # Pre-loaded or lazy-loaded on first tick
        # Per-robot movement cooldown accumulator (seconds).
        self._move_cooldowns: dict[uuid.UUID, float] = {}
        # Cached robot_id → task_type ("RETRIEVE" | "RETURN" | None).
        self._robot_task_types: dict[str, str | None] = {}
        # Cached robot_id → hold_pick_task_id (tote possession).
        self._robot_tote_cache: dict[str, str | None] = {}
        # Idle point reservation: (row,col) → robot_id — prevents multiple
        # K50H robots from targeting the same idle point simultaneously.
        self._idle_point_claims: dict[tuple[int, int], uuid.UUID] = {}
        # Idle-blocker cooldown: robot_id → tick when last parked.
        self._blocker_park_cooldown: dict[uuid.UUID, int] = {}
        # Per-robot same-cell reroute tracking: robot_id -> {"cell": (r,c), "count": int, "first_tick": int}
        self._reroute_stuck: dict[uuid.UUID, dict] = {}
        # Orphan debug rate limiter: robot_id -> {"tick": int, "sig": str}
        self._orphan_debug_last: dict[uuid.UUID, dict] = {}
        # Cached queue/approach/station cells for queue-entrance nudge logic.
        # Rebuilt by _advance_all_queues every 5 ticks.
        self._queue_area_cells: set[tuple[int, int]] = set()
        # OCCUPIED block tracker: robot_id -> {"blocker_id": UUID, "cell": (r,c), "ticks": int}
        # Tracks how long a robot has been blocked by the SAME blocker at the SAME cell.
        self._occupied_block_tracker: dict[uuid.UUID, dict] = {}
        # Yield cooldown: robot_id -> tick until which queue advance should NOT
        # re-route this robot.  Set after OccupiedBreaker backoff or QueueNudge.
        self._yield_cooldown: dict[uuid.UUID, int] = {}
        # Per-station ordered queue chain for pull-based FIFO.
        # station_id_str → {
        #   "chain_cells": [(r,c), ...],   # [A, Q1, Q2, ..., Qn]
        #   "chain_rids": [rid_str|None, ...],  # occupant at each cell
        #   "station_name": str,
        #   "station_id": UUID,
        # }
        # A = approach/serve position (index 0), Qn = entry point (index -1).
        self._queue_chains: dict[str, dict] = {}
        # Pending admission: station_id_str → [robot_id_str, ...]
        # Robots waiting to enter the station queue (Qn full).
        self._queue_pending: dict[str, list[str]] = {}

    async def update(self, dt: float) -> None:
        """Called once per simulation tick.

        Parameters
        ----------
        dt:
            Elapsed simulation time in seconds for this tick (used for
            interpolation weighting, currently one discrete step per tick).
        """
        self._tick_counter += 1
        from src.wes.application.station_queue_service import set_current_tick
        set_current_tick(self._tick_counter)
        self._traffic.set_tick(self._tick_counter)
        # On first tick, reserve starting cells and sync positions.
        if self._robots is not None and self._tick_counter == 1:
            for r in self._robots:
                self._traffic.set_position(r.grid_row, r.grid_col, r.id)
            # Immediately publish in-memory positions so snapshot_builder
            # serves correct coordinates from the very first tick.
            import src.shared.simulation_state as _init_sim
            _init_live: dict[str, dict] = {}
            for r in self._robots:
                _rt = r.type.value if hasattr(r.type, "value") else str(r.type)
                _st = r.status.value if hasattr(r.status, "value") else str(r.status)
                _init_live[str(r.id)] = {"row": r.grid_row, "col": r.grid_col, "heading": r.heading, "status": _st}
            _init_sim.robot_positions = _init_live
        if self._robots is None:
            # Load robots using a FRESH session (the FleetManager's session
            # from simulation_start may be closed by now).
            from src.shared.database import async_session_factory as _asf_load
            from src.ess.domain.models import Robot as _RobotModel
            from sqlalchemy import select as _sel_load
            try:
                async with _asf_load() as _load_sess:
                    _result = await _load_sess.execute(_sel_load(_RobotModel))
                    self._robots = list(_result.scalars().all())
                    for r in self._robots:
                        _load_sess.expunge(r)
            except Exception:
                logger.exception("Failed to load robots on first tick")
                self._robots = []
            for r in self._robots:
                self._traffic.set_position(r.grid_row, r.grid_col, r.id)
        robots = self._robots
        robot_map = {r.id: r for r in robots}
        position_updates: dict[str, dict] = {}

        import src.shared.simulation_state as simulation_state
        import src.shared.simulation_state as _park_sim  # alias used by parking/reroute logic
        speed_cfg = simulation_state.robot_speed

        for robot in robots:
            # Skip robots waiting at station UNLESS they now have a path
            # (meaning the return flow has started and released them).
            if robot.status == RobotStatus.WAITING_FOR_STATION:
                path = await self._cache.get_path(robot.id)
                if path:
                    # Return flow started — sync status to MOVING everywhere.
                    robot.status = RobotStatus.MOVING
                    robot.hold_at_station = False  # type: ignore[attr-defined]
                    await self._cache.update_status(
                        robot.id, RobotStatus.MOVING.value
                    )
                    # Broadcast immediately so frontend sees the transition.
                    robot_type = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
                    remaining = [[r, c] for r, c in path]
                    position_updates[str(robot.id)] = {
                        "id": str(robot.id),
                        "name": robot.name,
                        "type": robot_type,
                        "row": robot.grid_row,
                        "col": robot.grid_col,
                        "heading": robot.heading,
                        "status": RobotStatus.MOVING.value,
                        "path": remaining,
                        "task_type": self._robot_task_types.get(str(robot.id)),
                        "hold_pick_task_id": self._robot_tote_cache.get(str(robot.id)),
                        "wait_ticks": self._wait_counts.get(robot.id, 0),
                    }
                    logger.info(
                        "K50H %s released from WAITING_FOR_STATION (return path found)",
                        robot.id,
                    )
                else:
                    continue

            path = await self._cache.get_path(robot.id)
            if not path:
                robot._next_cell = None  # type: ignore[attr-defined]
                # A WAITING robot with no path is stuck — reset to IDLE
                # and try to replan path from its active equipment task.
                # BUT: do NOT reset robots waiting in a station queue —
                # they are legitimately waiting for the queue to advance.
                at_queue = getattr(robot, "_at_queue_cell", False)
                # Also treat robots carrying totes as queue-bound — the
                # _at_queue_cell flag can be lost during reroutes, but the
                # tote cache is authoritative (refreshed from DB every tick).
                has_tote = str(robot.id) in self._robot_tote_cache
                # Safety net: a robot with an active equipment task must not
                # be reset to IDLE — tote pickup timing (per-aisle handoff)
                # can lag behind the task assignment.
                has_task = self._robot_task_types.get(str(robot.id)) is not None
                if at_queue or has_tote or has_task:
                    # Recovery: K50H stuck IDLE with RETURN task and no path
                    # → replan path to cantilever.  This happens when the
                    # arrival event crashed (e.g. is_edge NameError) and the
                    # return flow never completed.
                    _rt_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
                    _task_type = self._robot_task_types.get(str(robot.id))
                    if (
                        _rt_str == "K50H"
                        and robot.status == RobotStatus.IDLE
                        and _task_type == "RETURN"
                    ):
                        await self._replan_return_to_cantilever(robot)
                    continue  # Stay at queue cell, do not park
                if robot.status == RobotStatus.WAITING:
                    robot.status = RobotStatus.IDLE
                    await self._cache.update_status(
                        robot.id, RobotStatus.IDLE.value
                    )
                    logger.info(
                        "Reset %s from WAITING to IDLE (no path)", robot.name,
                    )
                    # Attempt to replan path from active K50H_MOVING task
                    await self._replan_from_active_task(robot)
                # Park IDLE robots that sit on critical cells (rack_edge_row
                # or aisle rows) — they block other robots otherwise.
                if (
                    robot.status == RobotStatus.IDLE
                    and self._grid is not None
                ):
                    # Queue zone protection: FIFO controller handles queue-area robots
                    if self._is_in_queue_zone(robot.grid_row, robot.grid_col, robot=robot):
                        continue

                    _rt_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)

                    on_station = self._grid[robot.grid_row][robot.grid_col] == CellType.STATION

                    # K50H: park to idle point if available, or off station/critical cells
                    if _rt_str == "K50H":
                        if _park_sim.idle_points:
                            at_idle = (robot.grid_row, robot.grid_col) in _park_sim.idle_points
                            if not at_idle:
                                await self._park_to_idle_point(robot, robot.grid_row, robot.grid_col)
                            continue
                        # No idle points: at least get off station/aisle/rack_edge
                        if on_station or robot.grid_row in _park_sim.aisle_rows:
                            await self._park_one_step(robot, robot.grid_row, robot.grid_col)
                        elif _park_sim.rack_edge_row is not None and robot.grid_row == _park_sim.rack_edge_row:
                            await self._park_one_step(robot, robot.grid_row, robot.grid_col)
                        continue

                    # A42TD: stay in aisle (1 per aisle). Move off rack_edge or non-aisle.
                    # Skip if already parked (prevents oscillation).
                    if getattr(robot, "_parked", False):
                        pass
                    else:
                        on_rack_edge = (
                            _park_sim.rack_edge_row is not None
                            and robot.grid_row == _park_sim.rack_edge_row
                        )
                        on_aisle = robot.grid_row in _park_sim.aisle_rows

                        # If already in an aisle within territory, don't
                        # park even if on rack_edge.  A42TD's "home" is the
                        # aisle — forcing it off rack_edge causes oscillation
                        # when aisle == rack_edge or the only parking target
                        # is another critical cell.
                        _terr_ok = True
                        if on_aisle:
                            _tc, _tr = self._get_territory(robot)
                            if _tc and not (_tc[0] <= robot.grid_col <= _tc[1]):
                                _terr_ok = False
                            if _tr and not (_tr[0] <= robot.grid_row <= _tr[1]):
                                _terr_ok = False

                        should_park = False
                        if on_aisle and _terr_ok:
                            # Already in a valid aisle spot — no parking needed.
                            pass
                        elif on_rack_edge or on_station:
                            should_park = True
                        elif not on_aisle and _park_sim.aisle_rows:
                            should_park = True

                        if should_park:
                            await self._park_one_step(robot, robot.grid_row, robot.grid_col)
                            robot._parked = True  # type: ignore[attr-defined]
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

            # SAFETY: reject non-adjacent moves (prevents teleportation from
            # stale/wrong paths planned with an incorrect start position).
            _dr = abs(target_row - robot.grid_row)
            _dc = abs(target_col - robot.grid_col)
            if _dr + _dc > 1:
                logger.warning(
                    "Non-adjacent move rejected for %s: (%d,%d)->(%d,%d), clearing stale path",
                    robot.name, robot.grid_row, robot.grid_col, target_row, target_col,
                )
                await self._cache.set_path(robot.id, [])
                robot._next_cell = None  # type: ignore[attr-defined]
                continue

            # Try to reserve the target cell.
            reserved = self._traffic.reserve_cell(
                target_row, target_col, robot.id
            )

            if not reserved:
                # Cell blocked -- robot waits.
                self._wait_counts[robot.id] = self._wait_counts.get(robot.id, 0) + 1

                # Blocked cell diagnostics for broadcast.
                _bi = self._traffic.get_cell_block_info(
                    next_cell[0], next_cell[1], robot.id,
                )
                _bn = None
                if _bi["blocked_by_rid"]:
                    _blk = robot_map.get(uuid.UUID(_bi["blocked_by_rid"]))
                    _bn = _blk.name if _blk else _bi["blocked_by_rid"][:8]

                # Update OCCUPIED block tracker (same blocker + same cell).
                if _bi["blocked_reason"] == "OCCUPIED" and _bi["blocked_by_rid"]:
                    _obt_bid = uuid.UUID(_bi["blocked_by_rid"])
                    _obt = self._occupied_block_tracker.get(robot.id)
                    if _obt and _obt["blocker_id"] == _obt_bid and _obt["cell"] == next_cell:
                        _obt["ticks"] += 1
                    else:
                        self._occupied_block_tracker[robot.id] = {
                            "blocker_id": _obt_bid, "cell": next_cell, "ticks": 1,
                        }
                else:
                    self._occupied_block_tracker.pop(robot.id, None)

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
                        "hold_pick_task_id": self._robot_tote_cache.get(str(robot.id)),
                        "wait_ticks": self._wait_counts.get(robot.id, 0),
                        "blocked_cell": [next_cell[0], next_cell[1]],
                        "blocked_reason": _bi["blocked_reason"],
                        "blocked_by": _bn,
                        "blocked_age": _bi.get("reservation_age_ticks"),
                    }

                # Congestion-aware reroute after threshold.
                if (
                    self._wait_counts[robot.id] >= _REROUTE_WAIT_THRESHOLD
                    and self._planner is not None
                    and self._grid is not None
                ):
                    # ── Queue zone protection: FIFO controller handles queue robots ──
                    if self._is_in_queue_zone(robot.grid_row, robot.grid_col):
                        continue

                    # ── Blocked cell diagnostics ──
                    block_info = _bi
                    blocker_name = _bn

                    # ── Same-cell reroute loop detection ──
                    stuck = self._reroute_stuck.get(robot.id)
                    if stuck and stuck["cell"] == next_cell:
                        stuck["count"] += 1
                    else:
                        self._reroute_stuck[robot.id] = {
                            "cell": next_cell, "count": 1, "first_tick": self._tick_counter,
                        }
                        stuck = self._reroute_stuck[robot.id]

                    _STUCK_ESCALATION_THRESHOLD = 10
                    _STALE_RESERVATION_AGE = 30

                    # ── Escalation: after N consecutive same-cell reroutes ──
                    if stuck["count"] >= _STUCK_ESCALATION_THRESHOLD:
                        escalated = False
                        age = block_info.get("reservation_age_ticks") or 0

                        # Escalation A: stale forward reservation → force release
                        # Only releases _forward entries (not _position).
                        if age >= _STALE_RESERVATION_AGE and block_info["blocked_by_rid"]:
                            blocker_id_a = uuid.UUID(block_info["blocked_by_rid"])
                            blocker_robot_a = robot_map.get(blocker_id_a)
                            _physically_there = (
                                blocker_robot_a is not None
                                and blocker_robot_a.grid_row == next_cell[0]
                                and blocker_robot_a.grid_col == next_cell[1]
                            )
                            if not _physically_there:
                                released = self._traffic.force_release_stale(
                                    next_cell[0], next_cell[1], blocker_id_a,
                                )
                                if released:
                                    logger.warning(
                                        "ESCALATION-A: force-released STALE reservation at (%d,%d) "
                                        "held by %s for %d ticks (robot %s stuck %d reroutes)",
                                        next_cell[0], next_cell[1], blocker_name, age,
                                        robot.name, stuck["count"],
                                    )
                                    self._reroute_stuck.pop(robot.id, None)
                                    self._wait_counts[robot.id] = 0
                                    escalated = True

                        # Escalation B: real occupant → nudge ONLY if blocker is squatting
                        # 조건: idle/waiting이면서 tote 없고 path 없음
                        if not escalated and block_info["blocked_by_rid"]:
                            blocker_id_b = uuid.UUID(block_info["blocked_by_rid"])
                            blocker_robot_b = robot_map.get(blocker_id_b)
                            if blocker_robot_b is not None:
                                _has_tote = str(blocker_id_b) in self._robot_tote_cache
                                _has_path = bool(await self._cache.get_path(blocker_id_b))
                                _is_squatter = (
                                    blocker_robot_b.status in (RobotStatus.IDLE, RobotStatus.WAITING)
                                    and not _has_tote
                                    and not _has_path
                                )
                                if _is_squatter:
                                    await self._park_one_step(
                                        blocker_robot_b, blocker_robot_b.grid_row, blocker_robot_b.grid_col,
                                    )
                                    _nudge_path = await self._cache.get_path(blocker_id_b)
                                    if not _nudge_path:
                                        _bt = blocker_robot_b.type.value if hasattr(blocker_robot_b.type, "value") else str(blocker_robot_b.type)
                                        if _bt == "K50H":
                                            await self._park_to_idle_point(
                                                blocker_robot_b, blocker_robot_b.grid_row, blocker_robot_b.grid_col,
                                            )
                                        else:
                                            await self._park_to_floor(
                                                blocker_robot_b, blocker_robot_b.grid_row, blocker_robot_b.grid_col,
                                            )
                                    logger.warning(
                                        "ESCALATION-B: nudged squatter %s at (%d,%d) blocking %s for %d reroutes",
                                        blocker_name, next_cell[0], next_cell[1],
                                        robot.name, stuck["count"],
                                    )
                                    self._reroute_stuck.pop(robot.id, None)
                                    self._wait_counts[robot.id] = 0
                                    self._yield_cooldown[blocker_id_b] = self._tick_counter + 30
                                    escalated = True

                        if escalated:
                            continue

                    # ── Normal reroute (with rate limit) ──
                    congestion = self._traffic.get_congestion_map()
                    congestion[next_cell] = congestion.get(next_cell, 0.0) + 10000.0
                    for cell, occupant in self._traffic.occupied_cells.items():
                        if occupant != robot.id:
                            congestion[cell] = congestion.get(cell, 0.0) + 50.0
                            if cell[0] in _park_sim.aisle_rows:
                                congestion[cell] = congestion.get(cell, 0.0) + 200.0
                    planner = self._make_planner(robot=robot, congestion=congestion)
                    goal = path[-1]
                    new_path = planner.find_path(
                        (robot.grid_row, robot.grid_col), goal,
                    )
                    if new_path and len(new_path) > 1:
                        await self._cache.set_path(robot.id, new_path[1:])
                        logger.info(
                            "Rerouted %s around blocked cell (%d,%d) [%s by %s, age=%s] → %d steps",
                            robot.name, next_cell[0], next_cell[1],
                            block_info["blocked_reason"], blocker_name,
                            block_info.get("reservation_age_ticks"),
                            len(new_path) - 1,
                        )
                        # Rate-limited: don't reset to 0, wait threshold-1 ticks
                        self._wait_counts[robot.id] = _REROUTE_WAIT_THRESHOLD - 1
                        continue

                    # Step 2: Reroute failed — try nudging the blocker
                    # as a fallback.
                    blocker_id = self._traffic.occupied_cells.get(next_cell)
                    if blocker_id is not None and blocker_id != robot.id:
                        blocker_robot = robot_map.get(blocker_id)
                        if blocker_robot is not None:
                            # Allow nudging unless actively serving (WAITING_FOR_STATION + tote).
                            _blocker_has_tote = str(blocker_id) in self._robot_tote_cache
                            _can_nudge = (
                                blocker_robot.status != RobotStatus.WAITING_FOR_STATION
                                or not _blocker_has_tote
                            )
                            if _can_nudge:
                                blocker_path = await self._cache.get_path(blocker_id)
                                if not blocker_path:
                                    await self._park_one_step(
                                        blocker_robot,
                                        blocker_robot.grid_row,
                                        blocker_robot.grid_col,
                                    )
                                    # If single-step park failed, try multi-step.
                                    _nudge_path = await self._cache.get_path(blocker_id)
                                    if not _nudge_path:
                                        _bt = blocker_robot.type.value if hasattr(blocker_robot.type, "value") else str(blocker_robot.type)
                                        if _bt == "K50H":
                                            await self._park_to_idle_point(
                                                blocker_robot,
                                                blocker_robot.grid_row,
                                                blocker_robot.grid_col,
                                            )
                                        else:
                                            await self._park_to_floor(
                                                blocker_robot,
                                                blocker_robot.grid_row,
                                                blocker_robot.grid_col,
                                            )
                                    logger.info(
                                        "Nudged blocker %s at (%d,%d) out of way for %s",
                                        blocker_robot.name, blocker_robot.grid_row,
                                        blocker_robot.grid_col, robot.name,
                                    )
                                    self._yield_cooldown[blocker_id] = self._tick_counter + 30

                    logger.warning(
                        "Reroute failed for %s at (%d,%d) [%s by %s] → no alt path, nudge attempted",
                        robot.name, robot.grid_row, robot.grid_col,
                        block_info["blocked_reason"], blocker_name,
                    )
                    # Rate-limited reset
                    self._wait_counts[robot.id] = _REROUTE_WAIT_THRESHOLD - 1
                continue

            # Successfully reserved — clear wait counter and parked flag.
            self._wait_counts.pop(robot.id, None)
            self._reroute_stuck.pop(robot.id, None)
            self._occupied_block_tracker.pop(robot.id, None)
            robot._parked = False  # type: ignore[attr-defined]

            # Commit move: release old position, promote forward → position.
            self._traffic.confirm_move(
                robot.grid_row, robot.grid_col, target_row, target_col, robot.id,
            )

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

            # --- K50H tote pickup at cantilever (any rack-adjacent cell) ---
            # When a K50H with a reservation (but no tote) steps onto a
            # rack-adjacent cell, it physically picks up the tote from the
            # A42TD.  Per-aisle handoff means the cantilever can be at ANY
            # aisle row, not just the global rack_edge_row.
            _is_cantilever_cell = False
            if robot_type == "K50H" and str(robot.id) not in self._robot_tote_cache and self._grid is not None:
                for _dr, _dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    _nr, _nc = target_row + _dr, target_col + _dc
                    if 0 <= _nr < len(self._grid) and 0 <= _nc < len(self._grid[0]):
                        if self._grid[_nr][_nc] == CellType.RACK:
                            _is_cantilever_cell = True
                            break
            if _is_cantilever_cell:
                # Check DB: does this K50H have a reservation_pick_task_id?
                try:
                    from src.shared.database import async_session_factory as _asf_tote
                    from src.ess.domain.models import Robot as _RM_tote
                    async with _asf_tote() as _ts:
                        _r = await _ts.get(_RM_tote, robot.id)
                        if _r and _r.reservation_pick_task_id and not _r.hold_pick_task_id:
                            from src.wes.application.reservation_service import ReservationService as _RS_tote
                            _rsvc = _RS_tote(_ts)
                            await _rsvc.set_tote_possession(
                                robot.id,
                                pick_task_id=_r.reservation_pick_task_id,
                                at_station=False,
                            )
                            await _ts.commit()
                            self._robot_tote_cache[str(robot.id)] = str(_r.reservation_pick_task_id)
                            logger.info(
                                "K50H %s picked up tote at rack-edge (%d,%d) for pick_task %s",
                                robot.name, target_row, target_col, _r.reservation_pick_task_id,
                            )
                except Exception:
                    logger.exception("Failed to set tote possession for K50H %s", robot.id)

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
                "hold_pick_task_id": self._robot_tote_cache.get(str(robot.id)),
                "wait_ticks": self._wait_counts.get(robot.id, 0),
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
                # Skip if WAITING_FOR_STATION (holding tote at station),
                # WAITING (waiting in queue), or carrying a tote (must stay
                # near station queue, never park away).
                _has_tote_post = str(robot.id) in self._robot_tote_cache
                new_path = await self._cache.get_path(robot.id)
                if not new_path and self._grid is not None and not _has_tote_post and robot.status not in (RobotStatus.WAITING_FOR_STATION, RobotStatus.WAITING):
                    robot_type_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)

                    # K50H: go to idle point if available
                    if robot_type_str == "K50H" and _park_sim.idle_points:
                        at_idle = (target_row, target_col) in _park_sim.idle_points
                        if not at_idle:
                            await self._park_to_idle_point(robot, target_row, target_col)
                    else:
                        should_park = False
                        cell = self._grid[target_row][target_col]
                        if cell == CellType.STATION:
                            should_park = True
                        on_rack_edge = (
                            _park_sim.rack_edge_row is not None
                            and target_row == _park_sim.rack_edge_row
                        )
                        on_aisle = target_row in _park_sim.aisle_rows
                        if robot_type_str == "A42TD":
                            # A42TD: if in a valid aisle within territory,
                            # don't park (even if on rack_edge).
                            _a42_terr_ok = True
                            if on_aisle:
                                _tc2, _tr2 = self._get_territory(robot)
                                if _tc2 and not (_tc2[0] <= target_col <= _tc2[1]):
                                    _a42_terr_ok = False
                                if _tr2 and not (_tr2[0] <= target_row <= _tr2[1]):
                                    _a42_terr_ok = False
                            if on_aisle and _a42_terr_ok:
                                pass  # Valid aisle position — no parking
                            elif on_rack_edge:
                                should_park = True
                            elif not on_aisle and _park_sim.aisle_rows:
                                should_park = True
                        else:
                            # K50H: park off rack_edge and aisle
                            if on_rack_edge or on_aisle:
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
                    planner = self._make_planner(robot=robot)
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
        # Queue entrance blocker nudge: every tick (max 1 action)
        # ------------------------------------------------------------------
        await self._nudge_queue_blockers(robots, robot_map)

        # ------------------------------------------------------------------
        # OCCUPIED deadlock breaker: every tick (max 1 action)
        # ------------------------------------------------------------------
        await self._break_occupied_deadlocks(robots, robot_map)

        # ------------------------------------------------------------------
        # Periodic queue advance: enforce FIFO every 5 ticks
        # ------------------------------------------------------------------
        if self._tick_counter % 5 == 0:
            self._heal_reservation_ghosts(robots)
            await self._sweep_stale_reservations(robots)
            await self._advance_all_queues(robots)

        # ------------------------------------------------------------------
        # Pull-based queue advancement (every tick, lightweight in-memory)
        # ------------------------------------------------------------------
        if self._queue_chains:
            _pull_robot_map = {str(r.id): r for r in robots}
            await self._pull_advance_queues(_pull_robot_map)

        # ------------------------------------------------------------------
        # Periodic queue cleanup: fix stale queue states every 10 ticks
        # ------------------------------------------------------------------
        if self._tick_counter % 10 == 0:
            await self._cleanup_stale_queues(robots)

        # ------------------------------------------------------------------
        # Periodic retry: assign idle robots to orphaned equipment tasks
        # every 20 ticks (~2 seconds at normal speed).
        # ------------------------------------------------------------------
        if self._tick_counter % 20 == 0:
            await self._retry_orphaned_tasks(robots)

        # ------------------------------------------------------------------
        # Periodic retry: dispatch CREATED pick tasks that couldn't find
        # totes initially.  Every 50 ticks (~5 seconds).
        # ------------------------------------------------------------------
        if self._tick_counter % 50 == 0:
            await self._retry_created_pick_tasks()

        # ------------------------------------------------------------------
        # Periodic retry: SOURCE_REQUESTED pick tasks whose handler failed
        # (no EquipmentTask created).  Every 30 ticks (~3 seconds).
        # ------------------------------------------------------------------
        if self._tick_counter % 30 == 0:
            await self._retry_source_requested_tasks()

        # ------------------------------------------------------------------
        # Periodic retry: ALLOCATED orders with no PickTask (handler failed
        # before creating PickTask).  Every 40 ticks (~4 seconds).
        # ------------------------------------------------------------------
        if self._tick_counter % 40 == 0:
            await self._retry_allocated_orders()

        # ------------------------------------------------------------------
        # Deadlock detection and resolution
        # ------------------------------------------------------------------
        deadlocked_ids = self._traffic.detect_deadlock(robots)
        if deadlocked_ids:
            logger.warning("Deadlock detected among robots: %s", deadlocked_ids)
            await self._resolve_deadlock(robots, deadlocked_ids)

        # Broadcast position updates via WebSocket.
        if position_updates:
            # Enrich with cached target debug fields.
            for r in robots:
                _rid = str(r.id)
                if _rid in position_updates and hasattr(r, "_target_row"):
                    position_updates[_rid]["target_row"] = r._target_row
                    position_updates[_rid]["target_col"] = r._target_col
                    position_updates[_rid]["target_station"] = getattr(r, "_target_station", None)
            from src.shared.websocket_manager import ws_manager
            await ws_manager.broadcast_robot_updates(position_updates)

        # ------------------------------------------------------------------
        # Periodic heatmap + task-type refresh + idle point claim cleanup
        # ------------------------------------------------------------------
        if self._tick_counter % _HEATMAP_BROADCAST_INTERVAL == 0:
            await self._refresh_task_types()
            await self._broadcast_heatmap()
            await self._broadcast_allocation_stats()
            await self._broadcast_tote_origin_heatmap()
            await self._manage_idle_blockers(robots, robot_map)
            # Release idle point claims for robots that are no longer IDLE
            stale_claims = [
                pt for pt, rid in self._idle_point_claims.items()
                if rid in robot_map and robot_map[rid].status != RobotStatus.IDLE
            ]
            for pt in stale_claims:
                del self._idle_point_claims[pt]

        # ------------------------------------------------------------------
        # DB sync: write in-memory positions to the database every 5 ticks
        # AND on the very first tick so REST endpoints never serve (0,0).
        # ------------------------------------------------------------------
        if self._tick_counter <= 2 or self._tick_counter % 5 == 0:
            await self._sync_positions_to_db(robots)

        # ------------------------------------------------------------------
        # Sync in-memory positions to simulation_state for snapshot_builder.
        # ------------------------------------------------------------------
        _live: dict[str, dict] = {}
        for r in robots:
            _rt = r.type.value if hasattr(r.type, "value") else str(r.type)
            _st = r.status.value if hasattr(r.status, "value") else str(r.status)
            _rd: dict = {
                "row": r.grid_row,
                "col": r.grid_col,
                "heading": r.heading,
                "status": _st,
            }
            # Include cached target debug fields if present.
            if hasattr(r, "_target_row"):
                _rd["target_row"] = r._target_row
                _rd["target_col"] = r._target_col
                _rd["target_station"] = getattr(r, "_target_station", None)
            _live[str(r.id)] = _rd
        simulation_state.robot_positions = _live

    # ------------------------------------------------------------------
    # Territory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_territory(robot) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """Extract territory bounds from an A42TD robot.

        Returns (col_range, row_range) where each is (min, max) or None.
        """
        col_min = getattr(robot, "territory_col_min", None)
        col_max = getattr(robot, "territory_col_max", None)
        row_min = getattr(robot, "territory_row_min", None)
        row_max = getattr(robot, "territory_row_max", None)
        cols = (col_min, col_max) if col_min is not None and col_max is not None else None
        rows = (row_min, row_max) if row_min is not None and row_max is not None else None
        return (cols, rows)

    def _make_planner(
        self, robot=None, congestion=None, robot_type=None, aisle_rows=None,
        avoid_queue: bool = True,
    ) -> PathPlanner:
        """Create a PathPlanner with territory constraints for the given robot.

        Parameters
        ----------
        avoid_queue:
            When True (default), add all ``_queue_area_cells`` to the
            planner's avoid_cells so paths route around queue zones.
            Set to False when planning a path INTO a queue (FIFO routing).
        """
        import src.shared.simulation_state as _mp_sim
        _ar = aisle_rows if aisle_rows is not None else _mp_sim.aisle_rows
        _rtype = robot_type
        _territory_cols = None
        _territory_rows = None
        if robot is not None:
            _rt_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
            if _rtype is None:
                _rtype = RobotType(_rt_str) if _rt_str in ("K50H", "A42TD") else None
            if _rt_str == "A42TD":
                _territory_cols, _territory_rows = self._get_territory(robot)
        _avoid = self._queue_area_cells if avoid_queue else None
        return PathPlanner(
            self._grid, congestion=congestion, robot_type=_rtype,
            aisle_rows=_ar, territory_cols=_territory_cols,
            territory_rows=_territory_rows,
            avoid_cells=_avoid,
        )

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
        """Resolve a deadlock among the listed robots.

        Resolution stages (tried in order):
        0. SWAP — two robots wanting each other's cell (gate: wait >= 10 ticks,
           path <= 3 steps, neither carrying a tote).
        1. Park IDLE (no-path) robots out of the way.
        2. Reroute the robot with the shortest path (prefer non-carrying).
        3. Yield to an adjacent cell, then re-approach.
        4. Force-park to a FLOOR cell.
        5. Last resort: mark IDLE.
        """
        robot_map = {r.id: r for r in robots}

        # ── Stage 0: SWAP detection ────────────────────────────────
        # Two robots at adjacent cells each wanting the other's cell.
        _SWAP_MIN_WAIT = 10  # ticks blocked before swap is considered
        _SWAP_MAX_PATH = 3   # only short-path deadlocks qualify
        if len(deadlocked_ids) >= 2:
            for i, rid_a in enumerate(deadlocked_ids):
                robot_a = robot_map.get(rid_a)
                if robot_a is None:
                    continue
                # Gate: must be blocked long enough
                if self._wait_counts.get(rid_a, 0) < _SWAP_MIN_WAIT:
                    continue
                # Gate: not carrying a tote
                if str(rid_a) in self._robot_tote_cache:
                    continue
                next_a = getattr(robot_a, "_next_cell", None)
                if next_a is None:
                    continue
                path_a = await self._cache.get_path(rid_a)
                if not path_a or len(path_a) > _SWAP_MAX_PATH:
                    continue
                cell_a = (robot_a.grid_row, robot_a.grid_col)

                for rid_b in deadlocked_ids[i + 1:]:
                    robot_b = robot_map.get(rid_b)
                    if robot_b is None:
                        continue
                    if self._wait_counts.get(rid_b, 0) < _SWAP_MIN_WAIT:
                        continue
                    if str(rid_b) in self._robot_tote_cache:
                        continue
                    next_b = getattr(robot_b, "_next_cell", None)
                    if next_b is None:
                        continue
                    path_b = await self._cache.get_path(rid_b)
                    if not path_b or len(path_b) > _SWAP_MAX_PATH:
                        continue
                    cell_b = (robot_b.grid_row, robot_b.grid_col)

                    # Check swap condition: A wants B's cell AND B wants A's
                    if next_a != cell_b or next_b != cell_a:
                        continue

                    swapped = self._traffic.swap_cells(rid_a, cell_a, rid_b, cell_b)
                    if not swapped:
                        continue

                    # Update in-memory positions
                    robot_a.grid_row, robot_a.grid_col = cell_b
                    robot_b.grid_row, robot_b.grid_col = cell_a

                    # World state verification (R2)
                    if (robot_a.grid_row, robot_a.grid_col) != cell_b or \
                       (robot_b.grid_row, robot_b.grid_col) != cell_a:
                        # Rollback
                        self._traffic._position[cell_a] = rid_a
                        self._traffic._position[cell_b] = rid_b
                        robot_a.grid_row, robot_a.grid_col = cell_a
                        robot_b.grid_row, robot_b.grid_col = cell_b
                        logger.error(
                            "SWAP rollback: world state inconsistency for %s <-> %s",
                            robot_a.name, robot_b.name,
                        )
                        continue

                    # Compute headings
                    robot_a.heading = self._compute_heading(
                        cell_a[0], cell_a[1], cell_b[0], cell_b[1],
                    )
                    robot_b.heading = self._compute_heading(
                        cell_b[0], cell_b[1], cell_a[0], cell_a[1],
                    )

                    # Consume the swapped step from each path
                    await self._cache.set_path(rid_a, path_a[1:])
                    await self._cache.set_path(rid_b, path_b[1:])

                    # Update position caches
                    await self._cache.update_position(
                        rid_a, cell_b[0], cell_b[1], robot_a.heading,
                    )
                    await self._cache.update_position(
                        rid_b, cell_a[0], cell_a[1], robot_b.heading,
                    )

                    # Clear _next_cell (path consumed, next step TBD)
                    robot_a._next_cell = None  # type: ignore[attr-defined]
                    robot_b._next_cell = None  # type: ignore[attr-defined]

                    # Update statuses
                    for r, rid, remaining_path in [
                        (robot_a, rid_a, path_a[1:]),
                        (robot_b, rid_b, path_b[1:]),
                    ]:
                        if not remaining_path:
                            r.status = RobotStatus.IDLE
                            await self._cache.update_status(
                                rid, RobotStatus.IDLE.value,
                            )
                        else:
                            r.status = RobotStatus.MOVING
                            await self._cache.update_status(
                                rid, RobotStatus.MOVING.value,
                            )

                    self._wait_counts.pop(rid_a, None)
                    self._wait_counts.pop(rid_b, None)
                    logger.info(
                        "Deadlock resolved via SWAP: %s (%d,%d)<->(%d,%d) %s",
                        robot_a.name, cell_a[0], cell_a[1],
                        cell_b[0], cell_b[1], robot_b.name,
                    )
                    return  # Resolved

        # ── Stage 1: park IDLE (no-path) robots out of the way ─────
        for rid in deadlocked_ids:
            robot = robot_map.get(rid)
            if robot is None:
                continue
            path = await self._cache.get_path(rid)
            if not path and self._grid is not None:
                await self._park_to_floor(robot, robot.grid_row, robot.grid_col)
                parked_path = await self._cache.get_path(rid)
                if parked_path:
                    logger.info(
                        "Deadlock resolved: parked idle robot %s away from (%d,%d)",
                        rid, robot.grid_row, robot.grid_col,
                    )
                    return

        # ── Stage 2: reroute robot with shortest path ──────────────
        # Prefer non-carrying robots (D3 — tote protection).
        best_robot = None
        best_path_len = float("inf")
        best_has_tote = True
        for rid in deadlocked_ids:
            robot = robot_map.get(rid)
            if robot is None:
                continue
            path = await self._cache.get_path(rid)
            if not path:
                continue
            has_tote = str(rid) in self._robot_tote_cache
            if (len(path) < best_path_len) or \
               (len(path) == best_path_len and not has_tote and best_has_tote):
                best_path_len = len(path)
                best_robot = robot
                best_has_tote = has_tote

        if best_robot is None:
            return

        path = await self._cache.get_path(best_robot.id)
        if not path:
            return

        goal = path[-1]
        start = (best_robot.grid_row, best_robot.grid_col)
        await self._cache.set_path(best_robot.id, [])
        self._wait_counts.pop(best_robot.id, None)

        if self._grid is not None:
            congestion = self._traffic.get_congestion_map()
            for cell, occupant in self._traffic.occupied_cells.items():
                if occupant != best_robot.id:
                    congestion[cell] = congestion.get(cell, 0.0) + 50.0
            planner = self._make_planner(robot=best_robot, congestion=congestion)
            new_path = planner.find_path(start, goal)
            if new_path and len(new_path) > 1:
                await self._cache.set_path(best_robot.id, new_path[1:])
                logger.info(
                    "Deadlock resolved: rerouted robot %s", best_robot.id,
                )
            else:
                # ── Stage 3: yield to adjacent cell ────────────────
                yield_cell = self._find_yield_cell(best_robot, deadlocked_ids, robot_map)
                if yield_cell:
                    planner_clean = self._make_planner(robot=best_robot)
                    retreat_to_goal = planner_clean.find_path(yield_cell, goal)
                    if retreat_to_goal and len(retreat_to_goal) > 1:
                        await self._cache.set_path(best_robot.id, [yield_cell] + retreat_to_goal[1:])
                    else:
                        await self._cache.set_path(best_robot.id, [yield_cell])
                    logger.info(
                        "Deadlock resolved: yielded robot %s to (%d,%d)",
                        best_robot.id, yield_cell[0], yield_cell[1],
                    )
                else:
                    # ── Stage 4: force-park to any floor cell ──────
                    await self._park_to_floor(
                        best_robot, best_robot.grid_row, best_robot.grid_col,
                    )
                    parked = await self._cache.get_path(best_robot.id)
                    if parked:
                        logger.info(
                            "Deadlock resolved: force-parked %s from (%d,%d)",
                            best_robot.name, best_robot.grid_row, best_robot.grid_col,
                        )
                    else:
                        # ── Stage 5: last resort — mark IDLE ──────
                        best_robot.status = RobotStatus.IDLE
                        await self._cache.update_status(
                            best_robot.id, RobotStatus.IDLE.value
                        )
                        logger.warning(
                            "Deadlock unresolved: no escape for %s at (%d,%d)",
                            best_robot.id, best_robot.grid_row, best_robot.grid_col,
                        )

    def _find_yield_cell(
        self,
        robot,
        deadlocked_ids: list[uuid.UUID],
        robot_map: dict[uuid.UUID, object],
    ) -> tuple[int, int] | None:
        """Find any adjacent unoccupied traversable cell for the robot to yield into.

        Unlike ``_park_one_step``, this relaxes aisle/rack_edge restrictions
        because resolving a deadlock is more important than ideal positioning.
        """
        if self._grid is None:
            return None
        rows = len(self._grid)
        cols = len(self._grid[0])
        from_row, from_col = robot.grid_row, robot.grid_col
        robot_type_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)

        # Determine which cell types this robot can traverse.
        if robot_type_str == "K50H":
            impassable = {CellType.WALL}
        else:
            impassable = {CellType.WALL, CellType.RACK}

        occupied = self._traffic.occupied_cells
        candidates: list[tuple[float, int, int]] = []

        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = from_row + dr, from_col + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            cell_type = self._grid[nr][nc]
            if cell_type in impassable:
                continue
            if (nr, nc) in occupied:
                continue
            # Prefer FLOOR > AISLE > RACK etc.  Lower score = better.
            score = 0.0
            if cell_type == CellType.FLOOR:
                score = 0.0
            elif cell_type == CellType.AISLE:
                score = 1.0
            elif cell_type == CellType.RACK:
                score = 2.0
            elif cell_type == CellType.IDLE_POINT:
                score = 0.5
            else:
                score = 3.0
            candidates.append((score, nr, nc))

        if not candidates:
            return None
        candidates.sort()
        return (candidates[0][1], candidates[0][2])

    # ------------------------------------------------------------------
    # Task-type cache
    # ------------------------------------------------------------------

    async def _refresh_task_types(self) -> None:
        """Refresh the robot_id → task_type mapping from active EquipmentTasks,
        and the robot_id → hold_pick_task_id mapping (tote possession)."""
        from src.shared.database import async_session_factory
        from src.ess.domain.models import EquipmentTask, Robot as RobotModel
        from sqlalchemy import select

        mapping: dict[str, str | None] = {}
        tote_mapping: dict[str, str | None] = {}
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

                # Fetch tote possession for all robots
                robot_result = await session.execute(select(RobotModel))
                for r in robot_result.scalars():
                    if r.hold_pick_task_id:
                        tote_mapping[str(r.id)] = str(r.hold_pick_task_id)
        except Exception:
            return  # Keep previous mapping on error
        self._robot_task_types = mapping
        self._robot_tote_cache = tote_mapping

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

    async def _broadcast_allocation_stats(self) -> None:
        """Send allocation distribution stats to all connected WS clients."""
        from src.wes.application.allocation_engine import get_allocation_stats
        stats = get_allocation_stats()
        if not stats.get("stations"):
            return
        from src.shared.websocket_manager import ws_manager
        await ws_manager.broadcast("allocation_skew.updated", stats)

    async def _broadcast_tote_origin_heatmap(self) -> None:
        """Send the tote origin heatmap to all connected WS clients."""
        from src.ess.application.tote_origin_tracker import get_tracker
        tracker = get_tracker()
        allocated = tracker.get_allocated_map()
        completed = tracker.get_completed_map()
        if not allocated and not completed:
            return
        from src.shared.websocket_manager import ws_manager
        await ws_manager.broadcast("tote_origin_heatmap.updated", {
            "allocated": {f"{r},{c}": v for (r, c), v in allocated.items()},
            "completed": {f"{r},{c}": v for (r, c), v in completed.items()},
        })

    # ------------------------------------------------------------------
    # Queue zone helper
    # ------------------------------------------------------------------

    def _is_in_queue_zone(self, row: int, col: int, robot=None, radius: int = 0) -> bool:
        """Return True if (row, col) is a queue area cell AND the robot is
        assigned to a queue (has _target_station or _at_queue_cell).

        A robot merely *passing through* near queue cells is NOT protected —
        only robots that are queue participants get the FIFO-only shield.

        Parameters
        ----------
        radius : int
            Manhattan distance from queue cells.  Default 0 = exact match only.
        robot : optional
            If provided, also checks queue membership.  A robot that is not
            a queue participant is never considered "in queue zone".
        """
        if not self._queue_area_cells:
            return False

        # Position check
        in_zone = (row, col) in self._queue_area_cells
        if not in_zone and radius > 0:
            in_zone = any(
                abs(row - qr) + abs(col - qc) <= radius
                for qr, qc in self._queue_area_cells
            )
        if not in_zone:
            return False

        # If no robot provided, pure positional check
        if robot is None:
            return True

        # Robot must be a queue participant to be protected
        return (
            getattr(robot, "_at_queue_cell", False)
            or getattr(robot, "_target_station", None) is not None
        )

    # ------------------------------------------------------------------
    # Idle blocker management
    # ------------------------------------------------------------------

    async def _manage_idle_blockers(self, robots: list, robot_map: dict) -> None:
        """Move at most 1 task-less K50H off congested/bottleneck cell.

        Runs every 10 ticks.  Selects the single highest-scoring blocker
        (congestion * 100 + wait_ticks) and parks it to an idle point or
        FLOOR cell.  A 30-tick cooldown prevents re-parking the same robot.
        """
        if self._grid is None:
            return
        import src.shared.simulation_state as _bsim

        congestion = self._traffic.get_congestion_map()
        _CONG_THRESHOLD = 0.5
        _RACK_EDGE_PROX = 2
        _COOLDOWN_TICKS = 30
        _MIN_BLOCKED = 3

        best = None
        best_score = -1.0

        for robot in robots:
            rt = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
            if rt != "K50H":
                continue
            if robot.status not in (RobotStatus.IDLE, RobotStatus.WAITING):
                continue

            # Cooldown check
            last_tick = self._blocker_park_cooldown.get(robot.id, -999)
            if self._tick_counter - last_tick < _COOLDOWN_TICKS:
                continue

            path = await self._cache.get_path(robot.id)
            if path:
                continue
            if str(robot.id) in self._robot_tote_cache:
                continue
            if getattr(robot, "_at_queue_cell", False):
                continue
            # Queue zone protection: don't park robots in queue area
            if self._is_in_queue_zone(robot.grid_row, robot.grid_col, robot=robot):
                continue

            # Skip robots with an active equipment task
            if self._robot_task_types.get(str(robot.id)):
                continue

            pos = (robot.grid_row, robot.grid_col)
            cell_cong = congestion.get(pos, 0.0)
            wait_ticks = self._wait_counts.get(robot.id, 0)

            on_congested = cell_cong > _CONG_THRESHOLD
            near_rack_edge = (
                _bsim.rack_edge_row is not None
                and abs(robot.grid_row - _bsim.rack_edge_row) <= _RACK_EDGE_PROX
            )
            long_blocked = wait_ticks >= _MIN_BLOCKED

            if not (on_congested or (near_rack_edge and long_blocked)):
                continue

            # Already at idle point — no action needed
            if _bsim.idle_points and pos in _bsim.idle_points:
                continue

            # Skip IDLE robots on cells already handled by the per-tick
            # parking logic (station / aisle / rack_edge_row exactly).
            if robot.status == RobotStatus.IDLE:
                on_station = self._grid[robot.grid_row][robot.grid_col] == CellType.STATION
                on_aisle = robot.grid_row in _bsim.aisle_rows
                on_rack_edge_exact = (
                    _bsim.rack_edge_row is not None
                    and robot.grid_row == _bsim.rack_edge_row
                )
                if on_station or on_aisle or on_rack_edge_exact:
                    continue

            # Score: congestion first, then wait time
            score = cell_cong * 100 + wait_ticks
            if score > best_score:
                best_score = score
                best = robot

        if best is None:
            return

        logger.info(
            "IdleBlocker: parking K50H %s from (%d,%d) reason=%s",
            best.name, best.grid_row, best.grid_col,
            "CONGESTION" if congestion.get((best.grid_row, best.grid_col), 0) > _CONG_THRESHOLD else "BLOCKED",
        )

        if best.status == RobotStatus.WAITING:
            best.status = RobotStatus.IDLE
            await self._cache.update_status(best.id, RobotStatus.IDLE.value)

        if _bsim.idle_points:
            await self._park_to_idle_point(best, best.grid_row, best.grid_col)
        else:
            await self._park_to_floor(best, best.grid_row, best.grid_col)

        self._blocker_park_cooldown[best.id] = self._tick_counter

    # ------------------------------------------------------------------
    # Queue entrance blocker nudge
    # ------------------------------------------------------------------

    async def _nudge_queue_blockers(self, robots, robot_map: dict) -> None:
        """Nudge a robot that blocks queue/approach entrance (max 1/tick).

        When a K50H is WAITING to reach a queue/approach cell and another
        robot physically occupies the next cell (OCCUPIED, not RESERVED),
        attempt to move the blocker:

        - **Idle blocker** (no tote, no task, no path) → park to idle point.
        - **Active blocker** (has tote or task but no path, i.e. standing
          still) → backoff 1 cell so the queue-bound robot can pass.

        Limits: max 1 nudge per tick, 30-tick cooldown per blocker robot.
        """
        if not self._grid or not self._queue_area_cells:
            return

        import src.shared.simulation_state as _nqb_sim

        _COOLDOWN = 30
        _RADIUS = 2
        _MIN_WAIT = 3

        # Find WAITING robots blocked near queue area
        candidates: list[tuple] = []
        for robot in robots:
            if robot.status != RobotStatus.WAITING:
                continue
            wait_ticks = self._wait_counts.get(robot.id, 0)
            if wait_ticks < _MIN_WAIT:
                continue
            next_cell = getattr(robot, "_next_cell", None)
            if next_cell is None:
                continue
            # Must be targeting a station queue
            if not getattr(robot, "_target_station", None):
                continue

            # Check if next_cell is within RADIUS of any queue/approach cell
            near_queue = any(
                abs(next_cell[0] - qc[0]) + abs(next_cell[1] - qc[1]) <= _RADIUS
                for qc in self._queue_area_cells
            )
            if not near_queue:
                continue

            # Must be OCCUPIED (physical position), not RESERVED (forward)
            block_info = self._traffic.get_cell_block_info(
                next_cell[0], next_cell[1], robot.id,
            )
            if block_info["blocked_reason"] != "OCCUPIED":
                continue
            blocker_rid_str = block_info.get("blocked_by_rid")
            if not blocker_rid_str:
                continue

            blocker_id = uuid.UUID(blocker_rid_str)
            blocker = robot_map.get(blocker_id)
            if blocker is None:
                continue

            # Skip if blocker is WAITING_FOR_STATION (serving at station)
            if blocker.status == RobotStatus.WAITING_FOR_STATION:
                continue

            # Cooldown check on blocker
            last = self._blocker_park_cooldown.get(blocker_id, -999)
            if self._tick_counter - last < _COOLDOWN:
                continue

            candidates.append((robot, blocker, blocker_id, wait_ticks, next_cell))

        if not candidates:
            return

        # Pick the one with longest wait
        candidates.sort(key=lambda x: x[3], reverse=True)
        robot, blocker, blocker_id, wait_ticks, blocked_cell = candidates[0]

        # Queue zone protection: don't nudge a robot that is in the queue zone
        if self._is_in_queue_zone(blocker.grid_row, blocker.grid_col, robot=blocker):
            return

        # Determine blocker type
        _has_tote = str(blocker_id) in self._robot_tote_cache
        _has_task = bool(self._robot_task_types.get(str(blocker_id)))
        _has_path = bool(await self._cache.get_path(blocker_id))

        # Blocker is actively moving → don't interfere, let it finish
        if _has_path:
            return

        _is_idle_blocker = (
            blocker.status in (RobotStatus.IDLE, RobotStatus.WAITING)
            and not _has_tote
            and not _has_task
        )

        if _is_idle_blocker:
            # Case 1: idle blocker → park to idle point or one step
            bt = blocker.type.value if hasattr(blocker.type, "value") else str(blocker.type)
            if bt == "K50H" and _nqb_sim.idle_points:
                await self._park_to_idle_point(blocker, blocker.grid_row, blocker.grid_col)
            else:
                await self._park_one_step(blocker, blocker.grid_row, blocker.grid_col)
            # If park_one_step failed, try multi-step
            _npath = await self._cache.get_path(blocker_id)
            if not _npath:
                await self._park_to_floor(blocker, blocker.grid_row, blocker.grid_col)

            self._blocker_park_cooldown[blocker_id] = self._tick_counter
            self._yield_cooldown[blocker_id] = self._tick_counter + 30
            logger.warning(
                "QueueNudge: parked idle %s at (%d,%d) blocking %s → queue (waited %d)",
                blocker.name, blocker.grid_row, blocker.grid_col,
                robot.name, wait_ticks,
            )
        else:
            # Case 2: active blocker (has tote or task, no path) → backoff 1 cell
            await self._park_one_step(blocker, blocker.grid_row, blocker.grid_col)
            _npath = await self._cache.get_path(blocker_id)
            if not _npath:
                await self._park_to_floor(blocker, blocker.grid_row, blocker.grid_col)

            self._blocker_park_cooldown[blocker_id] = self._tick_counter
            self._yield_cooldown[blocker_id] = self._tick_counter + 30
            logger.warning(
                "QueueNudge: backed off active %s at (%d,%d) blocking %s → queue "
                "(tote=%s task=%s waited %d, yield_cd=%d)",
                blocker.name, blocker.grid_row, blocker.grid_col,
                robot.name, _has_tote, _has_task, wait_ticks,
                self._tick_counter + 30,
            )

    # ------------------------------------------------------------------
    # OCCUPIED deadlock breaker
    # ------------------------------------------------------------------

    async def _break_occupied_deadlocks(self, robots, robot_map: dict) -> None:
        """Break OCCUPIED-cell deadlocks persisting >= 10 ticks.

        When a robot has been blocked by the **same** blocker at the **same**
        cell for 10+ ticks (tracked by ``_occupied_block_tracker``):

        1. **SWAP** — if mutual (A blocks B AND B blocks A), atomically
           swap positions.  Works even when both carry totes.
        2. **Backoff** — lower-priority robot yields 1 cell.
           Priority: (no tote) > (no task) > (shorter path).
           Tote-carrying robots CAN yield if deadlocked.
        3. **Park** — if no yield cell, park a taskless robot to idle_point.

        Max 1 resolution per tick.  30-tick cooldown per yielded robot.
        """
        _THRESHOLD = 10
        _COOLDOWN = 30

        if not self._grid:
            return

        # Collect stuck entries past threshold
        candidates: list[tuple] = []
        for robot_id, info in list(self._occupied_block_tracker.items()):
            if info["ticks"] < _THRESHOLD:
                continue
            robot = robot_map.get(robot_id)
            blocker = robot_map.get(info["blocker_id"])
            if robot is None or blocker is None:
                continue
            # Queue zone protection: don't break deadlocks in queue area
            if self._is_in_queue_zone(robot.grid_row, robot.grid_col, robot=robot):
                continue
            if self._is_in_queue_zone(blocker.grid_row, blocker.grid_col, robot=blocker):
                continue
            # Cooldown on blocker
            last_b = self._blocker_park_cooldown.get(info["blocker_id"], -999)
            last_r = self._blocker_park_cooldown.get(robot_id, -999)
            if (self._tick_counter - last_b < _COOLDOWN
                    and self._tick_counter - last_r < _COOLDOWN):
                continue
            candidates.append((robot, blocker, info))

        if not candidates:
            return

        # Pick the longest-stuck pair
        candidates.sort(key=lambda x: x[2]["ticks"], reverse=True)
        robot, blocker, info = candidates[0]
        blocker_id = info["blocker_id"]
        robot_cell = (robot.grid_row, robot.grid_col)
        blocker_cell = (blocker.grid_row, blocker.grid_col)

        # ── SWAP detection: mutual blocking ──
        blocker_track = self._occupied_block_tracker.get(blocker_id)
        is_swap = (
            blocker_track is not None
            and blocker_track["blocker_id"] == robot.id
            and blocker_track["cell"] == robot_cell
        )

        if is_swap:
            path_a = await self._cache.get_path(robot.id)
            path_b = await self._cache.get_path(blocker_id)

            swapped = self._traffic.swap_cells(
                robot.id, robot_cell, blocker_id, blocker_cell,
            )
            if swapped:
                # Update positions
                robot.grid_row, robot.grid_col = blocker_cell
                blocker.grid_row, blocker.grid_col = robot_cell

                # Headings
                robot.heading = self._compute_heading(
                    robot_cell[0], robot_cell[1], blocker_cell[0], blocker_cell[1],
                )
                blocker.heading = self._compute_heading(
                    blocker_cell[0], blocker_cell[1], robot_cell[0], robot_cell[1],
                )

                # Consume 1 step from each path
                if path_a:
                    await self._cache.set_path(robot.id, path_a[1:])
                if path_b:
                    await self._cache.set_path(blocker_id, path_b[1:])

                # Update caches
                await self._cache.update_position(
                    robot.id, blocker_cell[0], blocker_cell[1], robot.heading,
                )
                await self._cache.update_position(
                    blocker_id, robot_cell[0], robot_cell[1], blocker.heading,
                )

                # Statuses
                for r, rid, rpath in [
                    (robot, robot.id, path_a),
                    (blocker, blocker_id, path_b),
                ]:
                    remaining = rpath[1:] if rpath else []
                    if not remaining:
                        r.status = RobotStatus.IDLE
                        await self._cache.update_status(rid, RobotStatus.IDLE.value)
                    else:
                        r.status = RobotStatus.MOVING
                        await self._cache.update_status(rid, RobotStatus.MOVING.value)

                # Clear trackers
                self._wait_counts.pop(robot.id, None)
                self._wait_counts.pop(blocker_id, None)
                self._occupied_block_tracker.pop(robot.id, None)
                self._occupied_block_tracker.pop(blocker_id, None)
                robot._next_cell = None  # type: ignore[attr-defined]
                blocker._next_cell = None  # type: ignore[attr-defined]

                logger.warning(
                    "OccupiedBreaker SWAP: %s (%d,%d)<->(%d,%d) %s (stuck %d ticks)",
                    robot.name, robot_cell[0], robot_cell[1],
                    blocker_cell[0], blocker_cell[1], blocker.name,
                    info["ticks"],
                )
                return

        # ── Not SWAP → priority-based backoff ──
        # Priority tuple: (has_tote, has_task, path_len) — lower = should yield
        r_tote = str(robot.id) in self._robot_tote_cache
        r_task = bool(self._robot_task_types.get(str(robot.id)))
        r_path = await self._cache.get_path(robot.id)
        r_plen = len(r_path) if r_path else 0

        b_tote = str(blocker_id) in self._robot_tote_cache
        b_task = bool(self._robot_task_types.get(str(blocker_id)))
        b_path = await self._cache.get_path(blocker_id)
        b_plen = len(b_path) if b_path else 0

        # Lower priority tuple yields first
        r_pri = (r_tote, r_task, r_plen)
        b_pri = (b_tote, b_task, b_plen)

        if r_pri <= b_pri:
            yielder, yielder_id = robot, robot.id
            other_name = blocker.name
        else:
            yielder, yielder_id = blocker, blocker_id
            other_name = robot.name

        # Try 1-cell yield
        yield_cell = self._find_yield_cell(
            yielder, [robot.id, blocker_id], robot_map,
        )
        if yield_cell:
            await self._cache.set_path(yielder_id, [yield_cell])
            self._blocker_park_cooldown[yielder_id] = self._tick_counter
            self._yield_cooldown[yielder_id] = self._tick_counter + 30
            self._occupied_block_tracker.pop(robot.id, None)
            self._occupied_block_tracker.pop(blocker_id, None)

            _y_tote = str(yielder_id) in self._robot_tote_cache
            _y_task = bool(self._robot_task_types.get(str(yielder_id)))
            logger.warning(
                "OccupiedBreaker BACKOFF: %s yields to (%d,%d) for %s "
                "(stuck %d, tote=%s task=%s, yield_cd=%d)",
                yielder.name, yield_cell[0], yield_cell[1], other_name,
                info["ticks"], _y_tote, _y_task,
                self._tick_counter + 30,
            )
            return

        # No yield cell → park a taskless robot to idle_point
        import src.shared.simulation_state as _ob_sim
        for r_candidate in [robot, blocker]:
            _c_tote = str(r_candidate.id) in self._robot_tote_cache
            _c_task = bool(self._robot_task_types.get(str(r_candidate.id)))
            if _c_tote or _c_task:
                continue
            if _ob_sim.idle_points:
                await self._park_to_idle_point(
                    r_candidate, r_candidate.grid_row, r_candidate.grid_col,
                )
            else:
                await self._park_to_floor(
                    r_candidate, r_candidate.grid_row, r_candidate.grid_col,
                )
            _npath = await self._cache.get_path(r_candidate.id)
            if _npath:
                self._blocker_park_cooldown[r_candidate.id] = self._tick_counter
                self._yield_cooldown[r_candidate.id] = self._tick_counter + 30
                self._occupied_block_tracker.pop(robot.id, None)
                self._occupied_block_tracker.pop(blocker_id, None)
                logger.warning(
                    "OccupiedBreaker PARK: %s to idle_point (stuck %d ticks, yield_cd=%d)",
                    r_candidate.name, info["ticks"], self._tick_counter + 30,
                )
                return

        logger.warning(
            "OccupiedBreaker: no resolution for %s blocked by %s at (%d,%d) "
            "(stuck %d ticks)",
            robot.name, blocker.name, info["cell"][0], info["cell"][1],
            info["ticks"],
        )

    # ------------------------------------------------------------------
    # DB position sync
    # ------------------------------------------------------------------

    async def _sync_positions_to_db(self, robots) -> None:
        """Batch-update all robot positions in the database.

        This ensures that REST endpoints (``GET /ess/robots``), snapshot
        broadcasts, and every other code path return the live in-memory
        positions instead of stale creation-time defaults.
        """
        try:
            from src.shared.database import async_session_factory as _asf_sync
            from src.ess.domain.models import Robot as _RobotSync
            from sqlalchemy import update as _sql_update

            async with _asf_sync() as _sync_sess:
                for r in robots:
                    _st = r.status.value if hasattr(r.status, "value") else str(r.status)
                    await _sync_sess.execute(
                        _sql_update(_RobotSync)
                        .where(_RobotSync.id == r.id)
                        .values(
                            grid_row=r.grid_row,
                            grid_col=r.grid_col,
                            heading=r.heading,
                            status=r.status,
                        )
                    )
                await _sync_sess.commit()
        except Exception:
            logger.debug("DB position sync failed (non-critical)")

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

        # Check if this FLOOR cell is a station's approach cell or queue cell.
        is_approach_cell = False
        is_queue_cell = False
        approach_station_id = None
        if cell_type == CellType.FLOOR and not is_rack_adjacent:
            from src.shared.database import async_session_factory as _asf_check
            from src.wes.domain.models import Station as _StCheck
            from sqlalchemy import select as _sel_check
            async with _asf_check() as _sess_check:
                # Check approach cell
                _st_result = await _sess_check.execute(
                    _sel_check(_StCheck).where(
                        _StCheck.approach_cell_row == target_row,
                        _StCheck.approach_cell_col == target_col,
                    ).limit(1)
                )
                _st = _st_result.scalar_one_or_none()
                if _st is not None:
                    is_approach_cell = True
                    approach_station_id = _st.id
                else:
                    # Check queue cells (Q1, Q2, Q3...)
                    _all_st = await _sess_check.execute(
                        _sel_check(_StCheck).where(
                            _StCheck.queue_cells_json.isnot(None),
                        )
                    )
                    import json as _json_check
                    for _st_q in _all_st.scalars():
                        try:
                            _qcells = _json_check.loads(_st_q.queue_cells_json)
                            for _qc in _qcells:
                                if _qc.get("row") == target_row and _qc.get("col") == target_col:
                                    is_queue_cell = True
                                    approach_station_id = _st_q.id
                                    break
                        except (ValueError, TypeError):
                            pass
                        if is_queue_cell:
                            break

        if cell_type not in (CellType.STATION, CellType.RACK) and not is_rack_adjacent and not is_approach_cell and not is_queue_cell:
            return

        # Handle queue cell arrival IMMEDIATELY — before the equipment task
        # check.  Queue arrival only needs to set WAITING and _at_queue_cell.
        # If we wait for the equipment task check below and it returns early
        # (no task found), the robot stays IDLE and the parking logic pulls
        # it away to an idle point.
        if is_queue_cell:
            robot.status = RobotStatus.WAITING
            robot._at_queue_cell = True  # type: ignore[attr-defined]
            await self._cache.update_status(
                robot.id, RobotStatus.WAITING.value
            )
            logger.info(
                "Robot %s arrived at queue cell (%d,%d) for station %s — waiting in queue (FIFO pull will advance)",
                robot.name, target_row, target_col, approach_station_id,
            )
            # FIFO pull (_pull_advance_queues) handles advancement every tick.
            # Do NOT call advance_queue() or _reroute_queue_robot() here —
            # those bypass the single-lane pull chain and jump robots to A.
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
            eq_task_state = eq_task.state
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

        if cell_type == CellType.STATION or is_approach_cell:
            from src.ess.domain.events import SourcePicked, SourceAtStation
            from src.wes.domain.enums import PickTaskState

            if pick_task_state == PickTaskState.SOURCE_AT_CANTILEVER:
                await event_bus.publish(SourcePicked(
                    pick_task_id=eq_pick_task_id,
                    tote_id=tote_id,
                    robot_id=robot.id,
                ))

            # Set robot to WAITING_FOR_STATION (holds tote, stays in front of station)
            robot.status = RobotStatus.WAITING_FOR_STATION
            await self._cache.update_status(
                robot.id, RobotStatus.WAITING_FOR_STATION.value
            )

            station_id = approach_station_id or pick_task_station_id
            await event_bus.publish(SourceAtStation(
                pick_task_id=eq_pick_task_id,
                tote_id=tote_id,
                station_id=station_id,
            ))

        # NOTE: is_queue_cell is handled early (above the equipment task
        # check) and returns before reaching here.

        elif cell_type == CellType.RACK or is_rack_adjacent:
            if eq_type == EquipmentTaskType.RETRIEVE:
                # Only handle rack-area arrivals during the A42TD leg.
                # Once K50H is dispatched (K50H_MOVING), rack-area arrivals
                # must be ignored — K50H goes to the station, not the rack.
                from src.ess.domain.enums import EquipmentTaskState
                if eq_task_state in (EquipmentTaskState.PENDING, EquipmentTaskState.A42TD_MOVING):
                    # Per-aisle handoff: ANY rack-adjacent cell is a valid
                    # cantilever point.  Fire SourceAtCantilever when the
                    # A42TD has reached its final destination (no remaining
                    # path).  If still moving, it's just passing through.
                    remaining_path = await self._cache.get_path(robot.id)
                    if not remaining_path:
                        from src.ess.domain.events import SourceAtCantilever
                        await event_bus.publish(SourceAtCantilever(
                            pick_task_id=eq_pick_task_id,
                            tote_id=tote_id,
                        ))
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
                elif is_rack_adjacent:
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

        planner = self._make_planner(robot=robot)
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
        robot_type_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)

        # For A42TD: find aisle rows already occupied by other idle A42TDs
        _crowded_aisles: set[int] = set()
        if robot_type_str == "A42TD" and self._robots:
            for other in self._robots:
                if other.id == robot.id:
                    continue
                ot = other.type.value if hasattr(other.type, "value") else str(other.type)
                if ot == "A42TD" and other.status == RobotStatus.IDLE:
                    if other.grid_row in _park_sim.aisle_rows:
                        _crowded_aisles.add(other.grid_row)

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
                # A42TD prefers aisle rows; K50H avoids them
                if robot_type_str == "A42TD":
                    if nr not in _park_sim.aisle_rows and _park_sim.aisle_rows:
                        continue
                    # Skip aisles that already have an idle A42TD
                    if nr in _crowded_aisles:
                        continue
                    # Stay within territory
                    _terr_cols, _terr_rows = self._get_territory(robot)
                    if _terr_cols and not (_terr_cols[0] <= nc <= _terr_cols[1]):
                        continue
                    if _terr_rows and not (_terr_rows[0] <= nr <= _terr_rows[1]):
                        continue
                else:
                    if nr in _park_sim.aisle_rows:
                        continue
                # Skip occupied cells
                if (nr, nc) in self._traffic.occupied_cells:
                    continue
                candidates.append((nr, nc))
        if candidates:
            target = candidates[0]
            await self._cache.set_path(robot.id, [target])
            return

        # Relaxed fallback: allow any traversable, unoccupied neighbour
        # (including aisle rows, rack cells for K50H) to unstick the robot.
        # For A42TD: still enforce aisle preference, territory, and block
        # IDLE_POINT cells (those are K50H-only parking spots).
        _impassable = {CellType.WALL} if robot_type_str == "K50H" else {CellType.WALL, CellType.RACK, CellType.IDLE_POINT}
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = from_row + dr, from_col + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if self._grid[nr][nc] in _impassable:
                    continue
                if (nr, nc) in self._traffic.occupied_cells:
                    continue
                # A42TD: only allow aisle rows even in relaxed mode
                if robot_type_str == "A42TD" and _park_sim.aisle_rows and nr not in _park_sim.aisle_rows:
                    continue
                # A42TD: enforce territory even in relaxed mode
                if robot_type_str == "A42TD":
                    _terr_cols, _terr_rows = self._get_territory(robot)
                    if _terr_cols and not (_terr_cols[0] <= nc <= _terr_cols[1]):
                        continue
                    if _terr_rows and not (_terr_rows[0] <= nr <= _terr_rows[1]):
                        continue
                await self._cache.set_path(robot.id, [(nr, nc)])
                return

        # No single-step candidate — try multi-step parking.
        await self._park_to_floor(robot, from_row, from_col)

    async def _park_to_floor(
        self, robot, from_row: int, from_col: int,
    ) -> None:
        """Move an idle robot off a key cell to the nearest FLOOR cell."""
        if self._grid is None:
            return
        import src.shared.simulation_state as _park_sim
        robot_type_str = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
        rows = len(self._grid)
        cols = len(self._grid[0])

        # For A42TD: find aisle rows already occupied by other idle A42TDs
        _crowded_aisles: set[int] = set()
        if robot_type_str == "A42TD" and self._robots:
            for other in self._robots:
                if other.id == robot.id:
                    continue
                ot = other.type.value if hasattr(other.type, "value") else str(other.type)
                if ot == "A42TD" and other.status == RobotStatus.IDLE:
                    if other.grid_row in _park_sim.aisle_rows:
                        _crowded_aisles.add(other.grid_row)

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
                        # A42TD prefers aisle rows; K50H avoids them
                        if robot_type_str == "A42TD":
                            if nr not in _park_sim.aisle_rows and _park_sim.aisle_rows:
                                continue
                            if nr in _crowded_aisles:
                                continue
                            # Stay within territory
                            _terr_cols, _terr_rows = self._get_territory(robot)
                            if _terr_cols and not (_terr_cols[0] <= nc <= _terr_cols[1]):
                                continue
                            if _terr_rows and not (_terr_rows[0] <= nr <= _terr_rows[1]):
                                continue
                        else:
                            if nr in _park_sim.aisle_rows:
                                continue
                        planner = self._make_planner(robot=robot)
                        path = planner.find_path(
                            (from_row, from_col), (nr, nc),
                        )
                        if path and len(path) > 1:
                            await self._cache.set_path(robot.id, path[1:])
                            return

    async def _park_to_idle_point(
        self, robot, from_row: int, from_col: int,
    ) -> None:
        """Move an idle K50H to the nearest available idle point.

        Uses ``_idle_point_claims`` to prevent multiple robots from
        targeting the same idle point.  A robot already sitting on an
        idle point keeps its claim; others must pick a different one.
        """
        if self._grid is None:
            return
        import src.shared.simulation_state as _park_sim
        if not _park_sim.idle_points:
            return

        pos = (from_row, from_col)

        # Already at an idle point → just register claim & done.
        if pos in _park_sim.idle_points:
            self._idle_point_claims[pos] = robot.id
            return

        # If this robot already has a valid claim, keep heading there.
        existing_claim = None
        for pt, rid in list(self._idle_point_claims.items()):
            if rid == robot.id:
                existing_claim = pt
                break
        if existing_claim is not None:
            # Check if we already have a path towards it
            path = await self._cache.get_path(robot.id)
            if path:
                return  # already en route
            # Claim still valid, replan path
            planner = PathPlanner(
                self._grid,
                robot_type=RobotType.K50H,
                aisle_rows=_park_sim.aisle_rows,
            )
            new_path = planner.find_path(pos, existing_claim)
            if new_path and len(new_path) > 1:
                await self._cache.set_path(robot.id, new_path[1:])
                return
            # Can't reach claimed point — release claim and find another
            del self._idle_point_claims[existing_claim]

        # Collect points that are already claimed or physically occupied.
        taken: set[tuple[int, int]] = set()
        for pt, rid in self._idle_point_claims.items():
            if rid != robot.id:
                taken.add(pt)
        for pt in _park_sim.idle_points:
            if pt in self._traffic.occupied_cells:
                occ_id = self._traffic.occupied_cells[pt]
                if occ_id != robot.id:
                    taken.add(pt)

        # Find nearest available idle point.
        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for pt in _park_sim.idle_points:
            if pt in taken:
                continue
            dist = abs(pt[0] - from_row) + abs(pt[1] - from_col)
            if dist < best_dist:
                best_dist = dist
                best = pt

        if best is None:
            # All idle points taken — fall back to regular parking.
            await self._park_one_step(robot, from_row, from_col)
            return

        # Claim this idle point and plan path.
        self._idle_point_claims[best] = robot.id
        planner = PathPlanner(
            self._grid,
            robot_type=RobotType.K50H,
            aisle_rows=_park_sim.aisle_rows,
        )
        path = planner.find_path(pos, best)
        if path and len(path) > 1:
            await self._cache.set_path(robot.id, path[1:])
            logger.info(
                "K50H %s claimed idle point (%d,%d) — %d steps",
                robot.name, best[0], best[1], len(path) - 1,
            )
        else:
            # Can't reach — release claim
            self._idle_point_claims.pop(best, None)
            await self._park_one_step(robot, from_row, from_col)

    async def _replan_from_active_task(self, robot) -> None:
        """When a K50H is stuck (WAITING→IDLE, no path), look up its active
        equipment task and replan the path to its target (station queue slot)."""
        if self._grid is None:
            return
        try:
            from src.shared.database import async_session_factory as _asf_rp
            from src.ess.domain.models import EquipmentTask
            from src.ess.domain.enums import EquipmentTaskState
            from src.wes.domain.models import Station
            from src.wes.application.station_queue_service import StationQueueService
            from sqlalchemy import select
            import src.shared.simulation_state as _rp_sim

            async with _asf_rp() as _sess:
                # Find K50H_MOVING task for this robot
                result = await _sess.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.k50h_robot_id == robot.id,
                        EquipmentTask.state == EquipmentTaskState.K50H_MOVING,
                    ).limit(1)
                )
                eq_task = result.scalar_one_or_none()
                if eq_task is None:
                    return

                from src.wes.domain.models import PickTask
                pt = await _sess.get(PickTask, eq_task.pick_task_id)
                if pt is None or pt.station_id is None:
                    return

                station = await _sess.get(Station, pt.station_id)
                if station is None:
                    return

                # Find next queue slot
                qsvc = StationQueueService(_sess)
                slot_name, slot_idx, slot_cell = await qsvc.find_next_slot(station.id)
                if slot_cell is not None:
                    target = slot_cell
                    await qsvc.place_in_slot(station.id, robot.id, slot_name, slot_idx)
                    await _sess.commit()
                elif station.approach_cell_row is not None:
                    target = (station.approach_cell_row, station.approach_cell_col)
                else:
                    target = (station.grid_row, station.grid_col)

            start = (robot.grid_row, robot.grid_col)
            if target and target != start:
                planner = PathPlanner(
                    self._grid,
                    robot_type=RobotType.K50H,
                    aisle_rows=_rp_sim.aisle_rows,
                )
                path = planner.find_path(start, target)
                if path and len(path) > 1:
                    await self._cache.set_path(robot.id, path[1:])
                    robot.status = RobotStatus.MOVING
                    await self._cache.update_status(robot.id, RobotStatus.MOVING.value)
                    logger.info(
                        "Replanned %s path to %s (%d steps)",
                        robot.name, target, len(path) - 1,
                    )
        except Exception:
            logger.exception("_replan_from_active_task failed for %s", robot.name)

    async def _replan_return_to_cantilever(self, robot) -> None:
        """Replan a stuck K50H with RETURN task back to the cantilever.

        Called when a K50H is IDLE with no path but has a RETURN equipment
        task — typically because a previous arrival event crashed.
        """
        if self._grid is None:
            return
        try:
            from src.shared.database import async_session_factory as _asf_ret
            from src.ess.domain.models import EquipmentTask
            from src.ess.domain.enums import EquipmentTaskState, EquipmentTaskType
            from sqlalchemy import select
            import src.shared.simulation_state as _ret_sim

            async with _asf_ret() as _sess:
                result = await _sess.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.k50h_robot_id == robot.id,
                        EquipmentTask.type == EquipmentTaskType.RETURN,
                        EquipmentTask.state.notin_(["COMPLETED"]),
                    ).limit(1)
                )
                eq_task = result.scalar_one_or_none()
                if eq_task is None:
                    return

            start = (robot.grid_row, robot.grid_col)
            # Find nearest cantilever (rack-edge) cell
            from src.handler_support import find_nearest_rack_edge
            target = find_nearest_rack_edge(
                _ret_sim.grid, start[0], start[1],
            )
            if target and target != start:
                planner = PathPlanner(
                    self._grid,
                    robot_type=RobotType.K50H,
                    aisle_rows=_ret_sim.aisle_rows,
                )
                path = planner.find_path(start, target)
                if path and len(path) > 1:
                    await self._cache.set_path(robot.id, path[1:])
                    robot.status = RobotStatus.MOVING
                    await self._cache.update_status(robot.id, RobotStatus.MOVING.value)
                    logger.info(
                        "Replanned RETURN %s → cantilever %s (%d steps)",
                        robot.name, target, len(path) - 1,
                    )
            elif target == start:
                # Already at cantilever — fire arrival event directly
                from src.shared.event_bus import event_bus
                from src.ess.domain.events import ReturnAtCantilever
                from src.wes.domain.models import PickTask
                async with _asf_ret() as _sess2:
                    result2 = await _sess2.execute(
                        select(EquipmentTask).where(
                            EquipmentTask.k50h_robot_id == robot.id,
                            EquipmentTask.type == EquipmentTaskType.RETURN,
                            EquipmentTask.state.notin_(["COMPLETED"]),
                        ).limit(1)
                    )
                    eq2 = result2.scalar_one_or_none()
                    if eq2:
                        pt = await _sess2.get(PickTask, eq2.pick_task_id)
                        if pt and pt.source_tote_id:
                            await event_bus.publish(ReturnAtCantilever(
                                pick_task_id=eq2.pick_task_id,
                                tote_id=pt.source_tote_id,
                            ))
                            logger.info(
                                "RETURN recovery: %s already at cantilever — fired ReturnAtCantilever",
                                robot.name,
                            )
        except Exception:
            logger.exception("_replan_return_to_cantilever failed for %s", robot.name)

    # ------------------------------------------------------------------
    # Pull-based FIFO queue chain
    # ------------------------------------------------------------------

    def _rebuild_queue_chains(self, stations, qsvc, robot_map: dict) -> None:
        """Rebuild per-station queue chains from DB station data.

        Chain order: front (A/approach) → Q1 → Q2 → ... → Qn (entry point).
        No holding cell — robots enter at Qn and pull forward to A.
        Called every 5 ticks from _advance_all_queues to stay in sync with DB.

        IMPORTANT: chain_rids (occupancy) is managed in-memory by the pull
        logic.  On subsequent builds (layout unchanged), we PRESERVE the
        in-memory chain_rids and only reconcile with newly dispatched robots
        from DB.  Reading chain_rids from DB every rebuild would overwrite
        the pull's in-memory state and cause robots to get stuck.
        """
        _first_build = not self._queue_chains
        _old_chains = dict(self._queue_chains)
        self._queue_chains.clear()
        for station in stations:
            a_cell = (
                station.approach_cell_row if station.approach_cell_row is not None else station.grid_row,
                station.approach_cell_col if station.approach_cell_col is not None else station.grid_col,
            )
            q_cells_raw = qsvc._get_queue_cells(station)

            # Use position-field order from _get_queue_cells (already sorted by position).
            # Q0=closest to approach, Qn=farthest (entry point).
            q_cells_sorted = [(qc["row"], qc["col"]) for qc in q_cells_raw]

            # Chain: [A, Q0(closest), Q1, ..., Qn(farthest/entry)]
            chain_cells = [a_cell] + q_cells_sorted

            if len(chain_cells) < 2:
                continue  # Need at least A + 1 Q slot

            sid = str(station.id)
            old_info = _old_chains.get(sid)

            if old_info and old_info["chain_cells"] == chain_cells:
                # ── Layout unchanged: preserve in-memory chain_rids ──
                chain_rids = list(old_info["chain_rids"])

                # Remove robots that no longer exist in robot_map
                for i, rid in enumerate(chain_rids):
                    if rid and rid not in robot_map:
                        chain_rids[i] = None

                # Reconcile: add newly dispatched robots from DB that
                # aren't yet in chain_rids (placed by arrival handlers).
                qs = qsvc._get_queue_state(station)
                db_rids: set[str] = set()
                for key in ("approach", "station"):
                    if qs.get(key):
                        db_rids.add(qs[key])
                for rid in qs.get("queue", []):
                    if rid:
                        db_rids.add(rid)

                chain_rid_set = {r for r in chain_rids if r}
                new_rids = db_rids - chain_rid_set
                for new_rid in new_rids:
                    r = robot_map.get(new_rid)
                    if r is None:
                        continue
                    rpos = (r.grid_row, r.grid_col)
                    # Place at physical position if it matches a chain cell
                    placed = False
                    for ci, cc in enumerate(chain_cells):
                        if cc == rpos and chain_rids[ci] is None:
                            chain_rids[ci] = new_rid
                            placed = True
                            break
                    if not placed:
                        # Robot dispatched but hasn't arrived yet — place at
                        # its DB slot (q_slots index maps to chain index+1).
                        # GUARD: only re-add if robot is actively moving
                        # (new dispatch).  IDLE/WAITING robots not at any
                        # chain cell are stale DB entries left over after
                        # pull reconciliation cleared them.
                        if r.status not in (RobotStatus.MOVING, RobotStatus.ASSIGNED):
                            continue
                        q_slots = qs.get("queue", [])
                        for qi, qrid in enumerate(q_slots):
                            if qrid == new_rid and qi + 1 < len(chain_rids) and chain_rids[qi + 1] is None:
                                chain_rids[qi + 1] = new_rid
                                break
            else:
                # ── First build or layout changed: populate from DB ──
                qs = qsvc._get_queue_state(station)
                chain_rids = [None] * len(chain_cells)

                # approach occupant (use "approach" or "station" if serving)
                _ap = qs.get("approach")
                _stn = qs.get("station")
                chain_rids[0] = _ap or _stn

                # Q occupants: direct 1:1 index mapping (both sorted by position)
                q_slots = qs.get("queue", [])
                for qi, rid in enumerate(q_slots):
                    if rid and qi + 1 < len(chain_rids):
                        chain_rids[qi + 1] = rid

            self._queue_chains[sid] = {
                "chain_cells": chain_cells,
                "chain_rids": chain_rids,
                "station_name": station.name,
                "station_id": station.id,
            }

            # Debug log: print chain coordinates once on first build
            if _first_build:
                _chain_str = " → ".join(
                    f"{'A' if i == 0 else f'Q{i}'}({c[0]},{c[1]})"
                    for i, c in enumerate(chain_cells)
                )
                logger.info(
                    "FIFO chain %s: %s (entry=Q%d, serve=A)",
                    station.name, _chain_str, len(chain_cells) - 1,
                )

        # Sync pending queue from shared simulation_state
        import src.shared.simulation_state as _ss_pend
        self._queue_pending = _ss_pend.queue_pending

    async def _pull_advance_queues(self, robot_map: dict) -> None:
        """Pull-based FIFO: advance robots 1 cell at a time, front→back.

        Runs every tick. For each station:
        0. Reconcile: remove robots that physically left the chain (e.g.
           return trip, released from station).
        1. Scan chain front→back: if cell[i] is empty and cell[i+1] has an
           arrived robot with no active path → set 1-cell path to pull forward.
        2. Only 1 move per station per tick (orderly advancement).
        3. If Qn (last cell / entry point) is empty → admit first pending robot.
        """
        for station_id, info in self._queue_chains.items():
            cells = info["chain_cells"]
            rids = info["chain_rids"]

            # ── Step 0: Reconcile — remove robots that left the chain ──
            # A robot that is not at its assigned cell AND not at any other
            # chain cell (i.e. it left the queue entirely, e.g. return trip)
            # must be cleared from chain_rids so the next robot can advance.
            chain_cell_set = set(cells)
            for i in range(len(cells)):
                if rids[i] is None:
                    continue
                r = robot_map.get(rids[i])
                if r is None:
                    rids[i] = None
                    continue
                rpos = (r.grid_row, r.grid_col)
                if rpos == cells[i]:
                    continue  # Still at assigned cell — OK
                # Robot physically left its assigned cell.
                # If it's at another chain cell, it's in transit (pull in
                # progress) — keep it.  Otherwise it left the queue.
                if rpos not in chain_cell_set:
                    logger.info(
                        "FIFO reconcile: %s left chain at %s (now at %s) — clearing slot %d at %s",
                        r.name, cells[i], rpos, i, info["station_name"],
                    )
                    rids[i] = None

            # ── Step 1: Pull chain — front → back (A → Q1 → Q2 → ... → Qn)
            for i in range(len(cells) - 1):
                if rids[i] is not None:
                    continue  # Front cell occupied, can't pull
                if rids[i + 1] is None:
                    continue  # Back cell empty, nothing to pull

                rid_str = rids[i + 1]
                try:
                    rid = uuid.UUID(rid_str)
                except (ValueError, TypeError):
                    continue
                r = robot_map.get(rid_str)
                if r is None:
                    continue

                # Robot must be physically at its current chain cell
                back_cell = cells[i + 1]
                if (r.grid_row, r.grid_col) != back_cell:
                    continue  # Not yet arrived at current slot

                # Must have no active path (finished moving to slot)
                if await self._cache.get_path(rid):
                    continue  # Still moving

                # Don't pull if robot is being served (WAITING_FOR_STATION)
                if r.status == RobotStatus.WAITING_FOR_STATION:
                    continue

                # Pull: move robot from cells[i+1] → cells[i] (adjacent 1 cell only)
                front_cell = cells[i]
                rids[i] = rid_str
                rids[i + 1] = None
                await self._cache.set_path(rid, [front_cell])
                r.status = RobotStatus.MOVING
                await self._cache.update_status(rid, RobotStatus.MOVING.value)
                logger.info(
                    "FIFO pull: %s %s → %s at %s",
                    r.name, back_cell, front_cell, info["station_name"],
                )
                break  # Only 1 move per station per tick

            # Admission: if last cell (Qn or approach) is empty → admit pending robot
            if len(cells) >= 1 and rids[-1] is None:
                pending = self._queue_pending.get(station_id, [])
                if pending:
                    next_rid_str = pending[0]
                    try:
                        next_rid = uuid.UUID(next_rid_str)
                    except (ValueError, TypeError):
                        pending.pop(0)
                        continue
                    r = robot_map.get(next_rid_str)
                    if r is None:
                        pending.pop(0)
                        continue
                    # Only admit if robot has no active path (waiting for admission)
                    if await self._cache.get_path(next_rid):
                        continue
                    pending.pop(0)
                    rids[-1] = next_rid_str
                    entry_cell = cells[-1]  # Qn = entry point
                    # Route to Qn via highway (avoid other stations' queue areas)
                    planner = self._make_planner(robot=r, avoid_queue=True)
                    path = planner.find_path((r.grid_row, r.grid_col), entry_cell)
                    if path and len(path) > 1:
                        await self._cache.set_path(next_rid, path[1:])
                        r.status = RobotStatus.MOVING
                        await self._cache.update_status(next_rid, RobotStatus.MOVING.value)
                        logger.info(
                            "FIFO admit: %s → Q%d %s at %s (%d steps)",
                            r.name, len(cells) - 1, entry_cell,
                            info["station_name"], len(path) - 1,
                        )
                    elif (r.grid_row, r.grid_col) == entry_cell:
                        # Already at entry cell
                        logger.info(
                            "FIFO admit: %s already at Q%d %s at %s",
                            r.name, len(cells) - 1, entry_cell,
                            info["station_name"],
                        )
                    else:
                        # Can't path — put back in pending
                        rids[-1] = None
                        pending.insert(0, next_rid_str)

    # ------------------------------------------------------------------
    # Periodic queue advance: enforce strict FIFO
    # ------------------------------------------------------------------

    async def _advance_all_queues(self, robots) -> None:
        """Advance all station queues and reroute robots to their correct
        FIFO positions.  Runs every few ticks to guarantee strict ordering
        even when event-driven triggers are missed."""
        try:
            from src.shared.database import async_session_factory as _asf_aq
            from src.wes.domain.models import Station
            from src.wes.application.station_queue_service import StationQueueService
            from src.handler_support import ws_broadcast
            from sqlalchemy import select
            import json as _json_aq

            robot_map = {str(r.id): r for r in robots}
            _robots_with_target: set[str] = set()  # track who got targets this cycle

            async with _asf_aq() as _sess:
                result = await _sess.execute(select(Station))
                stations = result.scalars().all()
                qsvc = StationQueueService(_sess)

                # Rebuild queue area cell cache for _nudge_queue_blockers
                self._queue_area_cells.clear()
                for _s in stations:
                    if _s.approach_cell_row is not None:
                        self._queue_area_cells.add((_s.approach_cell_row, _s.approach_cell_col))
                    self._queue_area_cells.add((_s.grid_row, _s.grid_col))
                    if _s.queue_cells_json:
                        try:
                            for _qc in _json_aq.loads(_s.queue_cells_json):
                                self._queue_area_cells.add((_qc["row"], _qc["col"]))
                        except Exception:
                            pass

                # Sync to shared state so handler path planning can avoid queue zones
                import src.shared.simulation_state as _ss_qa
                _ss_qa.queue_area_cells = set(self._queue_area_cells)

                # ── Phase 0: enforce queue invariants ──────────────
                # INV-1  Approach occupant MUST be at approach_cell
                #        (dist == 0), or be MOVING / WAITING_FOR_STATION.
                # INV-2  A robot may occupy at most ONE slot across ALL
                #        stations.  station+approach same robot while
                #        serving (WAITING_FOR_STATION) is the only
                #        allowed "duplicate" within a single station.
                try:
                    _inv_dirty_stations: set = set()

                    # -- Collect every (rid → slot) mapping globally --
                    _rid_slots: dict[str, list[tuple]] = {}  # rid → [(station, key, idx)]
                    for station in stations:
                        if not station.queue_state_json:
                            continue
                        try:
                            _qs = _json_aq.loads(station.queue_state_json)
                        except Exception:
                            continue
                        for _key in ("station", "approach"):
                            _rid = _qs.get(_key)
                            if _rid:
                                _rid_slots.setdefault(_rid, []).append(
                                    (station, _key, None)
                                )
                        for _qi, _slot_rid in enumerate(_qs.get("queue", [])):
                            if _slot_rid:
                                _rid_slots.setdefault(_slot_rid, []).append(
                                    (station, "queue", _qi)
                                )

                    # -- INV-2  Deduplicate: one rid → one slot --------
                    for _rid, _entries in _rid_slots.items():
                        # station+approach at SAME station for serving is OK
                        _real = [
                            e for e in _entries
                            if e[1] != "station"  # skip informational station slot
                        ]
                        if len(_real) <= 1:
                            continue  # no duplicate

                        # Pick the BEST slot: closest to physical position
                        _robot = robot_map.get(_rid)
                        _best = _real[0]
                        if _robot:
                            def _slot_dist(entry):
                                _s, _k, _i = entry
                                if _k == "approach":
                                    _r = _s.approach_cell_row if _s.approach_cell_row is not None else _s.grid_row
                                    _c = _s.approach_cell_col if _s.approach_cell_col is not None else _s.grid_col
                                elif _k == "queue" and _i is not None:
                                    _qc = qsvc._get_queue_cells(_s)
                                    if _i < len(_qc):
                                        _r, _c = _qc[_i]["row"], _qc[_i]["col"]
                                    else:
                                        return 999
                                else:
                                    return 999
                                return abs(_robot.grid_row - _r) + abs(_robot.grid_col - _c)
                            _best = min(_real, key=_slot_dist)

                        # Remove all non-best entries
                        for _entry in _entries:
                            if _entry is _best:
                                continue
                            # Also keep station slot if it matches approach
                            # (serving pair at same station)
                            if _entry[1] == "station" and _best[1] == "approach" and _entry[0] is _best[0]:
                                continue
                            _s, _k, _i = _entry
                            _qs2 = _json_aq.loads(_s.queue_state_json) if _s.queue_state_json else {}
                            if _k == "queue" and _i is not None:
                                _q = _qs2.get("queue", [])
                                if _i < len(_q) and _q[_i] == _rid:
                                    _q[_i] = None
                            elif _qs2.get(_k) == _rid:
                                _qs2[_k] = None
                            else:
                                continue  # nothing to clear
                            qsvc._save_queue_state(_s, _qs2, reason="inv2_dedup")
                            _inv_dirty_stations.add(_s.id)
                            _rname = _robot.name if _robot else _rid[:8]
                            logger.warning(
                                "INV-2 dedup: removed %s from %s[%s] at %s (kept %s[%s] at %s)",
                                _rname, _k, _i, _s.name,
                                _best[1], _best[2], _best[0].name,
                            )

                    # -- Approach self-heal: comprehensive validity check --
                    for station in stations:
                        if not station.queue_state_json:
                            continue
                        try:
                            _qs = _json_aq.loads(station.queue_state_json)
                        except Exception:
                            continue
                        _ap_rid = _qs.get("approach")
                        if not _ap_rid:
                            continue

                        # (a) station == approach same rid → clear approach
                        if _qs.get("station") == _ap_rid:
                            _qs["approach"] = None
                            _qs.pop("_approach_deadline_tick", None)
                            qsvc._save_queue_state(station, _qs, reason="approach_self_heal")
                            _inv_dirty_stations.add(station.id)
                            _r0 = robot_map.get(_ap_rid)
                            logger.warning(
                                "Approach heal: station==approach %s at %s → cleared",
                                _r0.name if _r0 else _ap_rid[:8], station.name,
                            )
                            continue

                        # (b) unknown robot → ghost
                        _ap_robot = robot_map.get(_ap_rid)
                        if _ap_robot is None:
                            _qs["approach"] = None
                            _qs.pop("_approach_deadline_tick", None)
                            if _qs.get("station") == _ap_rid:
                                _qs["station"] = None
                            qsvc._save_queue_state(station, _qs, reason="approach_self_heal")
                            _inv_dirty_stations.add(station.id)
                            logger.warning(
                                "Approach heal: unknown rid %s at %s → cleared",
                                _ap_rid[:8], station.name,
                            )
                            continue

                        _a_row = station.approach_cell_row if station.approach_cell_row is not None else station.grid_row
                        _a_col = station.approach_cell_col if station.approach_cell_col is not None else station.grid_col
                        _ap_dist = abs(_ap_robot.grid_row - _a_row) + abs(_ap_robot.grid_col - _a_col)

                        # (c) Robot is AT approach cell → valid, skip
                        if _ap_dist == 0:
                            continue

                        # -- Not at approach cell: check clear conditions --
                        _should_clear = False
                        _clear_reason = ""

                        # Condition A: truly idle — IDLE status AND no task AND
                        # no active path AND no tote.  A MOVING/WAITING robot
                        # with a path is actively en route and must NOT be cleared.
                        _ap_task = self._robot_task_types.get(_ap_rid)
                        _ap_has_path = bool(await self._cache.get_path(uuid.UUID(_ap_rid)))
                        _ap_has_tote = _ap_rid in self._robot_tote_cache
                        _ap_reserved = getattr(_ap_robot, 'reserved', False)
                        if (
                            _ap_robot.status == RobotStatus.IDLE
                            and not _ap_task
                            and not _ap_has_path
                            and not _ap_has_tote
                            and not _ap_reserved
                        ):
                            _should_clear = True
                            _clear_reason = f"truly_idle(task={_ap_task},path={_ap_has_path},tote={_ap_has_tote},rsv={_ap_reserved})"

                        # Condition B: targeting a different station
                        _ap_tgt_stn = getattr(_ap_robot, "_target_station", None)
                        if not _should_clear and _ap_tgt_stn is not None and _ap_tgt_stn != station.name:
                            _should_clear = True
                            _clear_reason = f"target_mismatch(target={_ap_tgt_stn})"

                        # Condition C: far away (dist > 1) with no transit evidence
                        if not _should_clear and _ap_dist > 1 and _ap_robot.status not in (
                            RobotStatus.MOVING, RobotStatus.WAITING,
                        ):
                            _should_clear = True
                            _st = _ap_robot.status.value if hasattr(_ap_robot.status, "value") else str(_ap_robot.status)
                            _clear_reason = f"far_no_transit(dist={_ap_dist},status={_st})"

                        # Condition D: TTL expired
                        _deadline = _qs.get("_approach_deadline_tick")
                        if not _should_clear and _deadline is not None and self._tick_counter > _deadline:
                            _should_clear = True
                            _clear_reason = f"ttl_expired(deadline={_deadline},now={self._tick_counter})"

                        if _should_clear:
                            _qs["approach"] = None
                            _qs.pop("_approach_deadline_tick", None)
                            if _qs.get("station") == _ap_rid:
                                _qs["station"] = None
                            qsvc._save_queue_state(station, _qs, reason="approach_self_heal")
                            _inv_dirty_stations.add(station.id)
                            logger.warning(
                                "Approach heal: cleared %s at %s dist=%d (%s)",
                                _ap_robot.name, station.name, _ap_dist, _clear_reason,
                            )

                    if _inv_dirty_stations:
                        await _sess.flush()
                        # Broadcast changes
                        for station in stations:
                            if station.id in _inv_dirty_stations:
                                _bqs = _json_aq.loads(station.queue_state_json) if station.queue_state_json else {}
                                await ws_broadcast("station.updated", {
                                    "id": str(station.id),
                                    "name": station.name,
                                    "current_robot_id": str(station.current_robot_id) if station.current_robot_id else None,
                                    "queue_state": _bqs,
                                    "queue_state_version_tick": _bqs.get("_version_tick"),
                                    "last_queue_mutation_reason": _bqs.get("_mutation_reason"),
                                })

                except Exception:
                    logger.exception("_advance_all_queues: invariant enforcement failed")

                # ── Phase 1: rebuild queue chains & sync to DB ──────
                try:
                    self._rebuild_queue_chains(stations, qsvc, robot_map)

                    # Sync chain state → DB queue_state_json
                    for station in stations:
                        sid = str(station.id)
                        info = self._queue_chains.get(sid)
                        if not info:
                            continue
                        cells = info["chain_cells"]
                        rids = info["chain_rids"]

                        # Build queue_state from chain
                        new_qs: dict = {}
                        new_qs["approach"] = rids[0]
                        # If approach has robot in WAITING_FOR_STATION → also set "station"
                        if rids[0]:
                            _ar = robot_map.get(rids[0])
                            if _ar and _ar.status == RobotStatus.WAITING_FOR_STATION:
                                new_qs["station"] = rids[0]
                            else:
                                new_qs["station"] = None
                        else:
                            new_qs["station"] = None
                        # Q slots: chain indices 1..end (all Q cells, no holding)
                        if len(rids) > 1:
                            new_qs["queue"] = list(rids[1:])
                        else:
                            new_qs["queue"] = []

                        _old_qs_json = station.queue_state_json
                        qsvc._save_queue_state(station, new_qs, reason="fifo_chain_sync")
                        if station.queue_state_json != _old_qs_json:
                            _saved_qs = qsvc._get_queue_state(station)
                            await ws_broadcast("station.updated", {
                                "id": str(station.id),
                                "name": station.name,
                                "current_robot_id": str(station.current_robot_id) if station.current_robot_id else None,
                                "queue_state": _saved_qs,
                                "queue_state_version_tick": _saved_qs.get("_version_tick"),
                                "last_queue_mutation_reason": _saved_qs.get("_mutation_reason"),
                            })

                        # Set target info on robots for debug display
                        for idx, rid_str in enumerate(rids):
                            if rid_str and rid_str in robot_map:
                                r = robot_map[rid_str]
                                r._target_row = cells[idx][0]  # type: ignore[attr-defined]
                                r._target_col = cells[idx][1]  # type: ignore[attr-defined]
                                r._target_station = info["station_name"]  # type: ignore[attr-defined]
                                _robots_with_target.add(rid_str)
                                r._at_queue_cell = (r.grid_row, r.grid_col) == cells[idx]  # type: ignore[attr-defined]
                                # Mark WAITING if at assigned cell and not serving
                                if r._at_queue_cell and r.status == RobotStatus.IDLE:
                                    r.status = RobotStatus.WAITING
                                    await self._cache.update_status(r.id, RobotStatus.WAITING.value)

                    # Also mark pending robots with target info
                    for station_id_str, pending_list in self._queue_pending.items():
                        info = self._queue_chains.get(station_id_str)
                        if not info:
                            continue
                        for prid in pending_list:
                            if prid in robot_map:
                                r = robot_map[prid]
                                r._target_station = info["station_name"]  # type: ignore[attr-defined]
                                r._target_row = info["chain_cells"][-1][0]  # type: ignore[attr-defined]
                                r._target_col = info["chain_cells"][-1][1]  # type: ignore[attr-defined]
                                _robots_with_target.add(prid)

                except Exception:
                    logger.exception("_advance_all_queues: chain sync failed")

                # ── Phase 1.5: evict squatters from queue cells ─────
                # A "squatter" is a robot physically at a queue cell but NOT
                # assigned to that slot (e.g. finished serving, waiting for
                # return path).  Evict it so the next queued robot can proceed.
                try:
                    for station in stations:
                        _qs_evict = qsvc._get_queue_state(station)
                        _a_row = station.approach_cell_row if station.approach_cell_row is not None else station.grid_row
                        _a_col = station.approach_cell_col if station.approach_cell_col is not None else station.grid_col

                        # Build list of queue cells → assigned occupant
                        _check_cells: list[tuple[tuple[int, int], str | None]] = [
                            ((_a_row, _a_col), _qs_evict.get("approach")),
                        ]
                        _q_cells_ev = qsvc._get_queue_cells(station)
                        _q_slots_ev = _qs_evict.get("queue", [])
                        for _qi, _qc in enumerate(_q_cells_ev):
                            _assigned = _q_slots_ev[_qi] if _qi < len(_q_slots_ev) else None
                            _check_cells.append(((_qc["row"], _qc["col"]), _assigned))

                        for (_cr, _cc), _assigned_rid in _check_cells:
                            _occ_id = self._traffic.occupied_cells.get((_cr, _cc))
                            if _occ_id is None:
                                continue
                            _occ_str = str(_occ_id)
                            if _occ_str == _assigned_rid:
                                continue  # Rightful occupant
                            # Check if occupant is assigned to ANY slot at this station
                            _all_assigned = set(filter(None, [
                                _qs_evict.get("station"),
                                _qs_evict.get("approach"),
                            ] + list(_q_slots_ev)))
                            if _occ_str in _all_assigned:
                                continue  # Assigned elsewhere at this station
                            # Squatter — check if evictable (strict guards)
                            _occ_robot = robot_map.get(_occ_str)
                            if _occ_robot is None:
                                continue
                            # GUARD: never evict queue-bound robots
                            from src.wes.application.station_queue_service import is_robot_in_any_queue as _is_in_q
                            if _is_in_q(_occ_id):
                                continue  # In queue index — protected
                            if getattr(_occ_robot, "_at_queue_cell", False):
                                continue  # Flagged as at queue cell — protected
                            if getattr(_occ_robot, "_target_station", None) is not None:
                                continue  # Has a station target — heading to queue
                            # GUARD: only evict idle/taskless/pathless/no-tote robots
                            _occ_has_tote = _occ_str in self._robot_tote_cache
                            _occ_task = self._robot_task_types.get(_occ_str)
                            if _occ_has_tote or _occ_task is not None:
                                continue  # Has tote or active task — protected
                            if _occ_robot.status not in (RobotStatus.IDLE, RobotStatus.WAITING):
                                continue  # Not idle/waiting — skip
                            _evict_path = await self._cache.get_path(_occ_id)
                            if _evict_path:
                                continue  # Already has a path — let it move
                            # Confirmed squatter: idle, no task, no tote, no path, no queue
                            _bt = _occ_robot.type.value if hasattr(_occ_robot.type, "value") else str(_occ_robot.type)
                            if _bt == "K50H":
                                await self._park_to_idle_point(
                                    _occ_robot, _occ_robot.grid_row, _occ_robot.grid_col,
                                )
                            else:
                                await self._park_one_step(
                                    _occ_robot, _occ_robot.grid_row, _occ_robot.grid_col,
                                )
                            logger.warning(
                                "Queue eviction: %s squatting (%d,%d) at %s → nudged (idle/taskless)",
                                _occ_robot.name, _cr, _cc, station.name,
                            )
                except Exception:
                    logger.exception("_advance_all_queues: queue cell eviction failed")

                # ── Phase 2: physical position sync ────────────────
                # Runs independently — even if main loop crashed above.
                # Build a lookup of ALL queue cells for fast matching.
                try:
                    _synced_stations: set = set()
                    _orphaned_info: list[str] = []

                    # Pre-build cell→(station, slot_name, slot_idx) lookup
                    _cell_lookup: dict[tuple[int, int], list[tuple]] = {}
                    for station in stations:
                        _a_row = station.approach_cell_row if station.approach_cell_row is not None else station.grid_row
                        _a_col = station.approach_cell_col if station.approach_cell_col is not None else station.grid_col
                        _cell_lookup.setdefault((_a_row, _a_col), []).append(
                            (station, "approach", None)
                        )
                        _q_cells = qsvc._get_queue_cells(station)
                        for _qi, _qc in enumerate(_q_cells):
                            _cell_lookup.setdefault((_qc["row"], _qc["col"]), []).append(
                                (station, "queue", _qi)
                            )

                    for rid_str, r in robot_map.items():
                        if rid_str in _robots_with_target:
                            continue
                        _rt_val = r.type.value if hasattr(r.type, "value") else str(r.type)
                        if _rt_val != "K50H":
                            continue
                        if r.status == RobotStatus.MOVING:
                            continue
                        _rpos = (r.grid_row, r.grid_col)
                        _registered = False

                        # Fast path: exact cell match via lookup
                        _candidates = _cell_lookup.get(_rpos, [])
                        for _stn, _slot_name, _slot_idx in _candidates:
                            _qs_now = qsvc._get_queue_state(_stn)
                            if _slot_name == "approach":
                                _ap_val = _qs_now.get("approach")
                                if not _ap_val:
                                    await qsvc.place_in_slot(_stn.id, r.id, "approach")
                                    _registered = True
                                    _synced_stations.add(_stn.id)
                                    logger.warning(
                                        "Physical sync: %s at approach → %s",
                                        r.name, _stn.name,
                                    )
                                    break
                                elif _ap_val != rid_str:
                                    # Ghost eviction: slot occupant not physically here
                                    _ghost_r = robot_map.get(_ap_val)
                                    if _ghost_r is None or (_ghost_r.grid_row, _ghost_r.grid_col) != _rpos:
                                        _gn = _ghost_r.name if _ghost_r else _ap_val[:8]
                                        await qsvc.place_in_slot(_stn.id, r.id, "approach")
                                        _registered = True
                                        _synced_stations.add(_stn.id)
                                        logger.warning(
                                            "Physical sync: %s at approach → %s (evicted ghost %s)",
                                            r.name, _stn.name, _gn,
                                        )
                                        break
                            elif _slot_name == "queue":
                                _q_slots = _qs_now.get("queue", [])
                                # Extend check for slot count mismatch
                                _slot_val = _q_slots[_slot_idx] if _slot_idx < len(_q_slots) else None
                                if not _slot_val:
                                    await qsvc.place_in_slot(_stn.id, r.id, "queue", _slot_idx)
                                    _registered = True
                                    _synced_stations.add(_stn.id)
                                    logger.warning(
                                        "Physical sync: %s at Q%d → %s",
                                        r.name, _slot_idx + 1, _stn.name,
                                    )
                                    break
                                elif _slot_val != rid_str:
                                    # Ghost eviction: slot occupant not physically here
                                    _ghost_r = robot_map.get(_slot_val)
                                    if _ghost_r is None or (_ghost_r.grid_row, _ghost_r.grid_col) != _rpos:
                                        _gn = _ghost_r.name if _ghost_r else _slot_val[:8]
                                        await qsvc.place_in_slot(_stn.id, r.id, "queue", _slot_idx)
                                        _registered = True
                                        _synced_stations.add(_stn.id)
                                        logger.warning(
                                            "Physical sync: %s at Q%d → %s (evicted ghost %s)",
                                            r.name, _slot_idx + 1, _stn.name, _gn,
                                        )
                                        break

                        if not _registered:
                            _orphaned_info.append(r)

                    # Broadcast station.updated for all physically synced stations
                    for station in stations:
                        if station.id in _synced_stations:
                            _updated_qs = qsvc._get_queue_state(station)
                            await ws_broadcast("station.updated", {
                                "id": str(station.id),
                                "name": station.name,
                                "current_robot_id": str(station.current_robot_id) if station.current_robot_id else None,
                                "queue_state": _updated_qs,
                                "queue_state_version_tick": _updated_qs.get("_version_tick"),
                                "last_queue_mutation_reason": _updated_qs.get("_mutation_reason"),
                            })

                    if _orphaned_info:
                        from src.wes.application.station_queue_service import is_robot_in_any_queue

                        _orphan_names: list[str] = []
                        _ORPHAN_RATE_LIMIT_TICKS = 10

                        for _orph in _orphaned_info:
                            _orph_st = _orph.status.value if hasattr(_orph.status, "value") else _orph.status
                            _orph_has_tote = str(_orph.id) in self._robot_tote_cache
                            _orph_task = self._robot_task_types.get(str(_orph.id))
                            _orph_tgt_row = getattr(_orph, "_target_row", None)
                            _orph_tgt_col = getattr(_orph, "_target_col", None)
                            _orph_tgt_stn = getattr(_orph, "_target_station", None)
                            _orph_in_idx = is_robot_in_any_queue(_orph.id)
                            _orph_at_qc = getattr(_orph, "_at_queue_cell", False)
                            _orph_parked = getattr(_orph, "_parked", False)
                            _orph_park_tick = self._blocker_park_cooldown.get(_orph.id)

                            # Blocked diagnostics (reuse TrafficController)
                            _orph_bc = None
                            _orph_br = None
                            _orph_bb = None
                            _orph_ba = None
                            _orph_next = getattr(_orph, "_next_cell", None)
                            if _orph_next:
                                _obi = self._traffic.get_cell_block_info(
                                    _orph_next[0], _orph_next[1], _orph.id,
                                )
                                if _obi["blocked_reason"] != "FREE":
                                    _orph_bc = _orph_next
                                    _orph_br = _obi["blocked_reason"]
                                    _ob_robot = robot_map.get(uuid.UUID(_obi["blocked_by_rid"])) if _obi["blocked_by_rid"] else None
                                    _orph_bb = _ob_robot.name if _ob_robot else (_obi["blocked_by_rid"][:8] if _obi["blocked_by_rid"] else None)
                                    _orph_ba = _obi.get("reservation_age_ticks")

                            # Build signature for rate limiting
                            _orph_sig = f"{_orph.grid_row},{_orph.grid_col}/{_orph_st}/{_orph_tgt_row},{_orph_tgt_col}"

                            # Rate limit: skip if same sig within last N ticks
                            _last = self._orphan_debug_last.get(_orph.id)
                            if _last and _last["sig"] == _orph_sig and (self._tick_counter - _last["tick"]) < _ORPHAN_RATE_LIMIT_TICKS:
                                _orphan_names.append(_orph.name)
                                continue
                            self._orphan_debug_last[_orph.id] = {"tick": self._tick_counter, "sig": _orph_sig}

                            # Target string
                            _tgt_str = "None"
                            if _orph_tgt_row is not None:
                                _tgt_str = f"{_orph_tgt_stn or '?'}@({_orph_tgt_row},{_orph_tgt_col})"

                            # Blocked string
                            _blk_str = "None"
                            if _orph_bc:
                                _blk_str = f"({_orph_bc[0]},{_orph_bc[1]})/{_orph_br}/by={_orph_bb} age={_orph_ba}"

                            logger.warning(
                                "[ORPHAN_DEBUG] rid=%s name=%s pos=(%d,%d) st=%s "
                                "has_tote=%s task=%s target=%s blocked=%s "
                                "in_queue_index=%s at_queue_cell=%s parked=%s last_park_tick=%s",
                                _orph.id, _orph.name, _orph.grid_row, _orph.grid_col, _orph_st,
                                _orph_has_tote, _orph_task, _tgt_str, _blk_str,
                                _orph_in_idx, _orph_at_qc, _orph_parked, _orph_park_tick,
                            )
                            _orphan_names.append(_orph.name)

                            # INDEX_DESYNC: robot in queue index but orphaned physically
                            if _orph_in_idx:
                                logger.warning(
                                    "[INDEX_DESYNC] %s (rid=%s) is in queue index but NOT at any queue cell "
                                    "pos=(%d,%d) st=%s",
                                    _orph.name, _orph.id, _orph.grid_row, _orph.grid_col, _orph_st,
                                )

                        logger.warning(
                            "Physical sync: %d orphaned K50H not at any queue cell: %s",
                            len(_orphaned_info), ", ".join(_orphan_names),
                        )

                        # ── Orphan recovery: re-queue displaced K50H ──
                        # Orphaned K50H with tote + active RETRIEVE task
                        # should be put back in queue_pending for FIFO
                        # re-admission.  Query DB for authoritative task
                        # type (cache can be stale up to 10 ticks).
                        from src.ess.domain.models import EquipmentTask as _ETOrph
                        from src.wes.domain.models import PickTask as _PTOrph
                        for _orph in _orphaned_info:
                            if _orph.status == RobotStatus.MOVING:
                                continue  # Actively moving — might be on return trip
                            _orph_rid = str(_orph.id)
                            _orph_has_tote = _orph_rid in self._robot_tote_cache
                            if not _orph_has_tote:
                                continue
                            # Find active RETRIEVE equipment task for this K50H
                            _eq_res = await _sess.execute(
                                select(_ETOrph).where(
                                    _ETOrph.k50h_robot_id == _orph.id,
                                    _ETOrph.state.notin_(["COMPLETED"]),
                                )
                            )
                            _eq_task = _eq_res.scalars().first()
                            if _eq_task is None:
                                continue
                            _eq_type = _eq_task.type.value if hasattr(_eq_task.type, "value") else str(_eq_task.type)
                            if _eq_type != "RETRIEVE":
                                continue
                            # Find station via pick_task
                            _pt = await _sess.get(_PTOrph, _eq_task.pick_task_id)
                            if _pt is None or _pt.station_id is None:
                                continue
                            _stn_key = str(_pt.station_id)
                            _pend = self._queue_pending.setdefault(_stn_key, [])
                            if _orph_rid not in _pend:
                                _pend.append(_orph_rid)
                                logger.warning(
                                    "Orphan recovery: re-queued %s to pending at station %s",
                                    _orph.name, _stn_key,
                                )
                except Exception:
                    logger.exception("_advance_all_queues: physical sync failed")

                # ── Phase 3: clear stale target caches ─────────────
                for rid_str, r in robot_map.items():
                    if rid_str not in _robots_with_target and hasattr(r, "_target_row"):
                        del r._target_row  # type: ignore[attr-defined]
                        del r._target_col  # type: ignore[attr-defined]
                        if hasattr(r, "_target_station"):
                            del r._target_station  # type: ignore[attr-defined]

                await _sess.commit()
        except Exception:
            logger.exception("_advance_all_queues failed")

    # ------------------------------------------------------------------
    # Queue cleanup
    # ------------------------------------------------------------------

    async def _cleanup_stale_queues(self, robots) -> None:
        """Remove stale robot references from station queues.

        If a robot is registered in a queue slot (station/approach/Q) but is
        no longer near that cell (e.g. it was released and moved away), clear
        the slot and advance the queue so other robots can proceed.
        """
        try:
            from src.shared.database import async_session_factory as _asf_cq
            from src.wes.domain.models import Station
            from src.wes.application.station_queue_service import StationQueueService
            from sqlalchemy import select
            import json as _json

            robot_map = {str(r.id): r for r in robots}

            async with _asf_cq() as _sess:
                result = await _sess.execute(select(Station))
                stations = result.scalars().all()
                changed_stations = []
                qsvc_cleanup = StationQueueService(_sess)

                for station in stations:
                    if not station.queue_state_json:
                        continue
                    try:
                        qs = _json.loads(station.queue_state_json)
                    except Exception:
                        continue

                    dirty = False

                    # Check station slot (robot physically stays at approach cell)
                    approach_row = station.approach_cell_row or station.grid_row
                    approach_col = station.approach_cell_col or station.grid_col

                    rid = qs.get("station")
                    if rid and rid in robot_map:
                        r = robot_map[rid]
                        dist = abs(r.grid_row - approach_row) + abs(r.grid_col - approach_col)
                        if dist > 2 and r.status not in (RobotStatus.WAITING_FOR_STATION,):
                            qs["station"] = None
                            # Only clear approach if it's the SAME robot (they
                            # share both slots while being served).  Don't nuke
                            # a different robot that's legitimately waiting.
                            if qs.get("approach") == rid:
                                qs["approach"] = None
                            station.current_robot_id = None
                            dirty = True
                            logger.info("Cleaned stale station slot: %s was at (%d,%d)", r.name, r.grid_row, r.grid_col)

                    # Approach self-heal (mirrors _advance_all_queues logic)
                    rid = qs.get("approach")
                    if rid:
                        # station == approach same rid
                        if qs.get("station") == rid:
                            qs["approach"] = None
                            qs.pop("_approach_deadline_tick", None)
                            dirty = True
                            _rn = robot_map[rid].name if rid in robot_map else rid[:8]
                            logger.info("Cleanup: station==approach %s at %s → cleared", _rn, station.name)
                        elif rid not in robot_map:
                            qs["approach"] = None
                            qs.pop("_approach_deadline_tick", None)
                            if qs.get("station") == rid:
                                qs["station"] = None
                            dirty = True
                            logger.info("Cleanup: unknown approach ghost rid=%s at %s", rid[:8], station.name)
                        else:
                            r = robot_map[rid]
                            dist = abs(r.grid_row - approach_row) + abs(r.grid_col - approach_col)
                            _cl_clear = False
                            _cl_reason = ""

                            if dist > 0:
                                # A) truly idle — not moving, no task, no path, no tote
                                _cl_task = self._robot_task_types.get(rid)
                                _cl_has_path = bool(await self._cache.get_path(uuid.UUID(rid)))
                                _cl_has_tote = rid in self._robot_tote_cache
                                _cl_reserved = getattr(r, 'reserved', False)
                                if (
                                    r.status == RobotStatus.IDLE
                                    and not _cl_task
                                    and not _cl_has_path
                                    and not _cl_has_tote
                                    and not _cl_reserved
                                ):
                                    _cl_clear = True
                                    _cl_reason = f"truly_idle"

                                # B) targeting different station
                                _cl_tgt = getattr(r, "_target_station", None)
                                if not _cl_clear and _cl_tgt is not None and _cl_tgt != station.name:
                                    _cl_clear = True
                                    _cl_reason = f"target={_cl_tgt}"

                                # C) far + no transit
                                if not _cl_clear and dist > 1 and r.status not in (
                                    RobotStatus.MOVING, RobotStatus.WAITING,
                                ):
                                    _cl_clear = True
                                    _cl_reason = f"far dist={dist}"

                                # D) TTL expired
                                _cl_dead = qs.get("_approach_deadline_tick")
                                if not _cl_clear and _cl_dead is not None and self._tick_counter > _cl_dead:
                                    _cl_clear = True
                                    _cl_reason = f"ttl_expired"

                            if _cl_clear:
                                qs["approach"] = None
                                qs.pop("_approach_deadline_tick", None)
                                if qs.get("station") == rid:
                                    qs["station"] = None
                                dirty = True
                                logger.info(
                                    "Cleanup: approach ghost %s at %s dist=%d (%s)",
                                    r.name, station.name, dist, _cl_reason,
                                )

                    # Check queue slots
                    q_slots = qs.get("queue", [])
                    queue_cells = []
                    if station.queue_cells_json:
                        try:
                            queue_cells = sorted(
                                _json.loads(station.queue_cells_json),
                                key=lambda c: c.get("position", 0),
                            )
                        except Exception:
                            pass
                    for i, slot_rid in enumerate(q_slots):
                        if slot_rid and slot_rid not in robot_map:
                            q_slots[i] = None
                            dirty = True
                            logger.info("Cleaned unknown Q%d ghost: rid=%s at %s", i+1, slot_rid[:8], station.name)
                            continue
                        if slot_rid and slot_rid in robot_map:
                            r = robot_map[slot_rid]
                            if i < len(queue_cells):
                                qc = queue_cells[i]
                                dist = abs(r.grid_row - qc["row"]) + abs(r.grid_col - qc["col"])
                                if dist > 2 and r.status not in (
                                    RobotStatus.WAITING_FOR_STATION,
                                    RobotStatus.MOVING,
                                ):
                                    q_slots[i] = None
                                    dirty = True
                                    logger.info("Cleaned ghost Q%d slot: %s at (%d,%d) dist=%d status=%s", i+1, r.name, r.grid_row, r.grid_col, dist, r.status.value if hasattr(r.status, "value") else r.status)


                    if dirty:
                        saved = qsvc_cleanup._save_queue_state(station, qs, reason="cleanup_stale")
                        if saved:
                            changed_stations.append(station.id)

                if changed_stations:
                    await _sess.flush()
                    # FIFO pull (_pull_advance_queues) handles advancement.
                    # Do NOT call advance_queue() or reroute here — those
                    # bypass the single-lane pull chain.
                    await _sess.commit()
        except Exception:
            logger.exception("_cleanup_stale_queues failed")

    # ------------------------------------------------------------------
    # Reservation ghost cleanup
    # ------------------------------------------------------------------

    def _heal_reservation_ghosts(self, robots) -> None:
        """Release stale forward reservations and fix position mismatches.

        Iterates _forward (not _position) since forward entries are the
        ones that can become stale.  Also validates _position entries.
        """
        robot_map = {r.id: r for r in robots}
        released = 0

        # 1) Clean stale forward reservations
        for (row, col), robot_id in list(self._traffic._forward.items()):
            robot = robot_map.get(robot_id)
            if robot is None:
                self._traffic.force_release_stale(row, col, robot_id)
                released += 1
                logger.warning(
                    "Reservation ghost: released forward (%d,%d) — unknown robot %s",
                    row, col, robot_id,
                )
                continue
            # If robot is not targeting this cell, it's stale
            _next = getattr(robot, "_next_cell", None)
            if _next != (row, col):
                self._traffic.force_release_stale(row, col, robot_id)
                released += 1
                logger.warning(
                    "Reservation ghost: %s forward (%d,%d) but next=%s → released",
                    robot.name, row, col, _next,
                )

        # 2) Fix position mismatches (robot moved but old position not cleaned up)
        for (row, col), robot_id in list(self._traffic._position.items()):
            robot = robot_map.get(robot_id)
            if robot is None:
                self._traffic.release_position(row, col, robot_id)
                released += 1
                logger.warning(
                    "Position ghost: released (%d,%d) — unknown robot %s",
                    row, col, robot_id,
                )
                continue
            if (robot.grid_row, robot.grid_col) != (row, col):
                self._traffic.release_position(row, col, robot_id)
                released += 1
                logger.warning(
                    "Position ghost: %s at (%d,%d) but position entry (%d,%d) → released",
                    robot.name, robot.grid_row, robot.grid_col, row, col,
                )

        if released:
            logger.info(
                "Reservation ghost cleanup: released %d stale entries",
                released,
            )

    # ------------------------------------------------------------------
    # Stale reservation TTL sweep
    # ------------------------------------------------------------------

    _STALE_RES_TTL = 60  # ticks before a forward reservation is considered stale
    _STALE_POS_TTL = 120  # ticks before an idle position is eligible for nudge+release

    async def _sweep_stale_reservations(self, robots) -> None:
        """Proactively release stale forward reservations and nudge+release
        idle robots sitting at positions for too long.

        Phase 1: _forward entries with age > TTL → unconditional force-release.
        Phase 2: _position entries with idle robot for > POS_TTL → nudge, then
                 release the position (guaranteeing released > 0 when stale).
        """
        robot_map = {r.id: r for r in robots}
        released = 0
        nudged = 0
        _log_budget = 5

        # ── Phase 1: stale forward reservations → release ──
        for (row, col), robot_id in list(self._traffic._forward.items()):
            res_tick = self._traffic._forward_tick.get((row, col))
            if res_tick is None:
                continue
            age = self._tick_counter - res_tick
            if age < self._STALE_RES_TTL:
                continue

            robot = robot_map.get(robot_id)
            robot_name = robot.name if robot else f"UNKNOWN({str(robot_id)[:8]})"

            # Check if robot is actively targeting this cell
            if robot is not None:
                _next = getattr(robot, "_next_cell", None)
                _tgt = None
                if hasattr(robot, "_target_row"):
                    _tgt = (robot._target_row, robot._target_col)
                if _next == (row, col) or _tgt == (row, col):
                    continue  # Robot intends to go here — leave it

            self._traffic.force_release_stale(row, col, robot_id)
            released += 1
            if _log_budget > 0:
                logger.warning(
                    "[STALE_RES_RELEASE] cell=(%d,%d) owner=%s age=%d (forward)",
                    row, col, robot_name, age,
                )
                _log_budget -= 1

        # ── Phase 2: idle robots at position too long → nudge + release ──
        for (row, col), robot_id in list(self._traffic._position.items()):
            robot = robot_map.get(robot_id)
            if robot is None:
                continue
            # Queue zone protection
            if self._is_in_queue_zone(row, col, robot=robot):
                continue
            if (robot.grid_row, robot.grid_col) != (row, col):
                continue  # Mismatch — healer will clean this
            # Cooldown: don't re-nudge a robot we recently nudged
            _last_park = self._blocker_park_cooldown.get(robot_id, 0)
            if self._tick_counter - _last_park < 30:
                continue
            _has_tote = str(robot.id) in self._robot_tote_cache
            _has_task = self._robot_task_types.get(str(robot.id)) is not None
            if _has_tote or _has_task:
                continue
            if robot.status not in (RobotStatus.IDLE, RobotStatus.WAITING):
                continue
            # Try to nudge; if nudge produces a path, release the position
            _rt = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
            _had_path_before = bool(await self._cache.get_path(robot.id))
            if _had_path_before:
                continue  # Already has a path — let it move
            if _rt == "K50H":
                await self._park_to_idle_point(robot, robot.grid_row, robot.grid_col)
            else:
                await self._park_one_step(robot, robot.grid_row, robot.grid_col)
            _has_path_after = bool(await self._cache.get_path(robot.id))
            if _has_path_after:
                # Nudge succeeded — release position so cell is immediately free
                self._traffic.release_position(row, col, robot.id)
                self._blocker_park_cooldown[robot.id] = self._tick_counter
                self._yield_cooldown[robot.id] = self._tick_counter + 30
                released += 1
                nudged += 1
                if _log_budget > 0:
                    logger.warning(
                        "[STALE_POS_RELEASE] cell=(%d,%d) owner=%s st=%s → nudged+released",
                        row, col, robot.name,
                        robot.status.value if hasattr(robot.status, "value") else robot.status,
                    )
                    _log_budget -= 1

        if released or nudged:
            logger.info(
                "Stale reservation sweep: released=%d nudged=%d (fwd_TTL=%d)",
                released, nudged, self._STALE_RES_TTL,
            )

    # ------------------------------------------------------------------
    # Periodic retry: orphaned equipment tasks
    # ------------------------------------------------------------------

    async def _retry_orphaned_tasks(self, robots) -> None:
        """Periodically find equipment tasks stuck without assigned robots
        and try to assign idle robots to them.

        Also detects K50H robots stranded at completed stations and triggers
        their return flow.
        """
        try:
            from src.shared.database import async_session_factory as _asf_retry
            from src.ess.domain.models import EquipmentTask, Robot as RobotModel
            from src.ess.domain.enums import EquipmentTaskState, EquipmentTaskType
            from src.wes.domain.models import PickTask, Station
            from src.wes.domain.enums import PickTaskState
            from sqlalchemy import select
            from src.shared.event_bus import event_bus
            import src.shared.simulation_state as _retry_sim

            if not _retry_sim.grid:
                return

            robot_map = {str(r.id): r for r in robots}
            pending_events: list = []

            async with _asf_retry() as session:
                # --- 1. Find PENDING equipment tasks without assigned robots ---
                # A42TD tasks without a42td_robot_id
                result = await session.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.state == EquipmentTaskState.PENDING,
                        EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                        EquipmentTask.a42td_robot_id.is_(None),
                    ).order_by(EquipmentTask.created_at).limit(5)
                )
                orphan_retrieves = result.scalars().all()

                for eq_task in orphan_retrieves:
                    # Skip if the tote is currently in use by another task
                    from src.handler_support import is_tote_in_use
                    if await is_tote_in_use(session, eq_task.pick_task_id):
                        continue

                    # Try to assign an idle A42TD
                    from src.ess.application.fleet_manager import FleetManager
                    from src.ess.domain.enums import RobotType
                    from src.ess.domain.models import Location
                    fm = FleetManager(session)
                    source_loc = await session.get(Location, eq_task.source_location_id) if eq_task.source_location_id else None
                    if source_loc is None:
                        continue

                    a42td = await fm.find_nearest_idle(
                        zone_id=source_loc.zone_id,
                        robot_type=RobotType.A42TD,
                        target_row=source_loc.grid_row,
                        target_col=source_loc.grid_col,
                        aisle_rows=_retry_sim.aisle_rows,
                    )
                    if a42td is None:
                        continue

                    await fm.assign_robot(a42td.id, eq_task.id)
                    eq_task.a42td_robot_id = a42td.id
                    await session.flush()

                    logger.info(
                        "Retry: assigned A42TD %s to orphaned RETRIEVE eq_task %s",
                        a42td.name, eq_task.id,
                    )

                    # Plan path to rack-edge (per-aisle: use A42TD's territory)
                    from src.handler_support import find_nearest_rack_edge, plan_and_store_path, get_robot_position, HandlerServices
                    pos = await get_robot_position(a42td.id)
                    start = pos or (a42td.grid_row, a42td.grid_col)
                    _a42_trows_s1 = None
                    if getattr(a42td, "territory_row_min", None) is not None:
                        _a42_trows_s1 = (a42td.territory_row_min, a42td.territory_row_max)
                    _a42_tcols_s1 = None
                    if getattr(a42td, "territory_col_min", None) is not None:
                        _a42_tcols_s1 = (a42td.territory_col_min, a42td.territory_col_max)
                    rack_edge = find_nearest_rack_edge(
                        _retry_sim.grid, source_loc.grid_row, source_loc.grid_col,
                        territory_rows=_a42_trows_s1,
                    )
                    if rack_edge:
                        svc = HandlerServices(session)
                        await plan_and_store_path(
                            svc, a42td.id, start, rack_edge,
                            robot_type=RobotType.A42TD,
                            territory_cols=_a42_tcols_s1,
                            territory_rows=_a42_trows_s1,
                        )
                    eq_task.state = EquipmentTaskState.A42TD_MOVING
                    await session.flush()

                # --- 1a. PENDING/A42TD_MOVING tasks WITH A42TD but stuck ---
                # The handler may have assigned an A42TD but failed to plan a
                # path, or the path was lost from Redis.  Re-plan and advance.
                result = await session.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.state.in_([
                            EquipmentTaskState.PENDING,
                            EquipmentTaskState.A42TD_MOVING,
                        ]),
                        EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                        EquipmentTask.a42td_robot_id.isnot(None),
                    ).order_by(EquipmentTask.created_at).limit(5)
                )
                stuck_with_a42td = result.scalars().all()

                for eq_task in stuck_with_a42td:
                    from src.ess.domain.enums import RobotType
                    from src.ess.domain.models import Location
                    from src.handler_support import find_nearest_rack_edge, plan_and_store_path, get_robot_position, HandlerServices
                    from src.ess.infrastructure.redis_cache import RobotStateCache
                    from src.shared.redis import get_redis

                    a42td_id = eq_task.a42td_robot_id
                    # Check if robot has a path in Redis
                    redis_client = await get_redis()
                    cache = RobotStateCache(redis_client)
                    existing_path = await cache.get_path(a42td_id)
                    if existing_path:
                        continue  # Path exists — robot should be moving

                    # Robot has no path. Check its position.
                    pos = await get_robot_position(a42td_id)
                    source_loc = (
                        await session.get(Location, eq_task.source_location_id)
                        if eq_task.source_location_id else None
                    )
                    if source_loc is None:
                        continue

                    # Get A42TD territory for per-aisle rack-edge
                    from src.ess.domain.models import Robot as _RModel
                    a42td_db = await session.get(_RModel, a42td_id)
                    _a42_trows_retry = None
                    if a42td_db and getattr(a42td_db, "territory_row_min", None) is not None:
                        _a42_trows_retry = (a42td_db.territory_row_min, a42td_db.territory_row_max)

                    rack_edge = find_nearest_rack_edge(
                        _retry_sim.grid, source_loc.grid_row, source_loc.grid_col,
                        territory_rows=_a42_trows_retry,
                    )
                    start = pos or (0, 0)

                    # If A42TD is already at the rack-edge → fire arrival event
                    if rack_edge and start == rack_edge:
                        # Advance to A42TD_MOVING if still PENDING
                        if eq_task.state == EquipmentTaskState.PENDING:
                            eq_task.state = EquipmentTaskState.A42TD_MOVING
                            await session.flush()
                        # Fire SourceAtCantilever
                        pt = await session.get(PickTask, eq_task.pick_task_id)
                        if pt and pt.source_tote_id:
                            from src.ess.domain.events import SourceAtCantilever
                            pending_events.append(SourceAtCantilever(
                                pick_task_id=eq_task.pick_task_id,
                                tote_id=pt.source_tote_id,
                            ))
                        logger.info(
                            "Retry: A42TD %s already at rack-edge %s — instant SourceAtCantilever (eq_task %s)",
                            a42td_id, rack_edge, eq_task.id,
                        )
                        continue

                    # Re-plan path to rack-edge
                    if rack_edge:
                        svc = HandlerServices(session)
                        territory_cols = None
                        territory_rows = None
                        from src.ess.domain.models import Robot as _RModel
                        a42td_db = await session.get(_RModel, a42td_id)
                        if a42td_db and a42td_db.territory_col_min is not None:
                            territory_cols = (a42td_db.territory_col_min, a42td_db.territory_col_max)
                        if a42td_db and getattr(a42td_db, "territory_row_min", None) is not None:
                            territory_rows = (a42td_db.territory_row_min, a42td_db.territory_row_max)
                        await plan_and_store_path(
                            svc, a42td_id, start, rack_edge,
                            robot_type=RobotType.A42TD,
                            territory_cols=territory_cols,
                            territory_rows=territory_rows,
                        )
                        if eq_task.state == EquipmentTaskState.PENDING:
                            eq_task.state = EquipmentTaskState.A42TD_MOVING
                        await session.flush()
                        logger.info(
                            "Retry: re-planned A42TD %s path → %s (eq_task %s, was stuck)",
                            a42td_id, rack_edge, eq_task.id,
                        )

                # K50H tasks at AT_CANTILEVER without k50h_robot_id
                result = await session.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
                        EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                        EquipmentTask.k50h_robot_id.is_(None),
                    ).order_by(EquipmentTask.created_at).limit(5)
                )
                orphan_at_cant = result.scalars().all()

                for eq_task in orphan_at_cant:
                    from src.ess.application.fleet_manager import FleetManager
                    from src.ess.domain.enums import RobotType
                    fm = FleetManager(session)
                    pt = await session.get(PickTask, eq_task.pick_task_id)
                    if pt is None or pt.station_id is None:
                        continue
                    station = await session.get(Station, pt.station_id)
                    if station is None:
                        continue
                    k50h = await fm.find_nearest_idle(
                        zone_id=station.zone_id,
                        robot_type=RobotType.K50H,
                        target_row=station.grid_row,
                        target_col=station.grid_col,
                        aisle_rows=_retry_sim.aisle_rows,
                    )
                    if k50h is None:
                        continue

                    await fm.assign_robot(k50h.id, eq_task.id)
                    eq_task.k50h_robot_id = k50h.id
                    eq_task.state = EquipmentTaskState.K50H_MOVING
                    await session.flush()
                    logger.info(
                        "Retry: assigned K50H %s to orphaned AT_CANTILEVER eq_task %s",
                        k50h.name, eq_task.id,
                    )

                    # ALWAYS use queue_pending — FIFO admission is the
                    # single entry point (prevents duplicate cell targeting).
                    import src.shared.simulation_state as _retry_ss
                    _pending = _retry_ss.queue_pending.setdefault(str(station.id), [])
                    _rid_str = str(k50h.id)
                    if _rid_str not in _pending:
                        _pending.append(_rid_str)
                    # FIFO admission will route when robot has no active path.
                    await session.flush()

                # --- 1b. RETURN tasks at AT_CANTILEVER without A42TD ---
                # _handle_return_at_cantilever may fail to find an A42TD.
                # The RETURN A42TD leg completes instantly (no physical move).
                result = await session.execute(
                    select(EquipmentTask).where(
                        EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
                        EquipmentTask.type == EquipmentTaskType.RETURN,
                        EquipmentTask.a42td_robot_id.is_(None),
                    ).order_by(EquipmentTask.created_at).limit(5)
                )
                orphan_return_cant = result.scalars().all()

                for eq_task in orphan_return_cant:
                    from src.ess.domain.enums import RobotType
                    from src.ess.domain.models import Location
                    fm = FleetManager(session)

                    target_loc = (
                        await session.get(Location, eq_task.target_location_id)
                        if eq_task.target_location_id else None
                    )
                    if target_loc is None:
                        continue

                    a42td = await fm.find_nearest_idle(
                        zone_id=target_loc.zone_id,
                        robot_type=RobotType.A42TD,
                        target_row=_retry_sim.rack_edge_row or target_loc.grid_row,
                        target_col=target_loc.grid_col,
                        aisle_rows=_retry_sim.aisle_rows,
                    )
                    if a42td is None:
                        continue

                    await fm.assign_robot(a42td.id, eq_task.id)
                    eq_task.a42td_robot_id = a42td.id
                    await session.flush()

                    # RETURN A42TD leg completes instantly (no physical movement).
                    svc = HandlerServices(session)
                    try:
                        await svc.executor.advance_task(eq_task.id, "k50h_dispatched")
                        await svc.executor.advance_task(eq_task.id, "delivered")
                        await svc.executor.advance_task(eq_task.id, "completed")
                    except ValueError:
                        logger.warning(
                            "Retry: RETURN eq_task %s advance failed (already in target state)",
                            eq_task.id,
                        )
                        continue

                    # Release A42TD immediately
                    pos = await get_robot_position(a42td.id)
                    await svc.fm.release_robot(a42td.id, eq_task.id, position=pos)
                    eq_task.a42td_robot_id = None
                    await session.flush()

                    # Publish SourceBackInRack to complete the PickTask
                    pt = await session.get(PickTask, eq_task.pick_task_id)
                    if pt and pt.source_tote_id:
                        from src.ess.domain.events import SourceBackInRack
                        pending_events.append(SourceBackInRack(
                            pick_task_id=eq_task.pick_task_id,
                            tote_id=pt.source_tote_id,
                            location_id=eq_task.target_location_id,
                        ))
                    logger.info(
                        "Retry: completed RETURN eq_task %s with A42TD %s (instant)",
                        eq_task.id, a42td.name,
                    )

                # --- 2. Detect stranded K50H at completed stations ---
                # Find K50H robots with hold_at_station=True where the pick task
                # is in a terminal-ish state but no RETURN task exists.
                result = await session.execute(
                    select(RobotModel).where(
                        RobotModel.hold_at_station == True,  # noqa: E712
                        RobotModel.hold_pick_task_id.isnot(None),
                    )
                )
                stranded_robots = result.scalars().all()
                for db_robot in stranded_robots:
                    pt = await session.get(PickTask, db_robot.hold_pick_task_id)
                    if pt is None:
                        continue
                    # Check if pick task is done (RETURN_REQUESTED or COMPLETED)
                    if pt.state not in (PickTaskState.RETURN_REQUESTED, PickTaskState.COMPLETED):
                        continue
                    # Check no RETURN equipment task exists
                    existing = await session.execute(
                        select(EquipmentTask).where(
                            EquipmentTask.pick_task_id == pt.id,
                            EquipmentTask.type == EquipmentTaskType.RETURN,
                        ).limit(1)
                    )
                    if existing.scalar_one_or_none() is not None:
                        continue

                    # Trigger return flow
                    from src.ess.domain.models import Tote as ToteModel
                    tote = await session.get(ToteModel, pt.source_tote_id) if pt.source_tote_id else None
                    if tote and tote.home_location_id:
                        from src.wes.domain.events import ReturnSourceTote
                        pending_events.append(ReturnSourceTote(
                            pick_task_id=pt.id,
                            tote_id=pt.source_tote_id,
                            target_location_id=tote.home_location_id,
                            station_id=pt.station_id,
                        ))
                        logger.info(
                            "Retry: triggering return for stranded K50H %s (pick_task %s)",
                            db_robot.name, pt.id,
                        )

                # --- 3. Clear stuck reserved flags ---
                # If a robot is IDLE + reserved=True but has no active
                # equipment task, the reservation flag is stale and prevents
                # find_nearest_idle from ever returning this robot.
                result = await session.execute(
                    select(RobotModel).where(
                        RobotModel.reserved == True,  # noqa: E712
                        RobotModel.status == RobotStatus.IDLE,
                    )
                )
                stuck_reserved = result.scalars().all()
                for db_robot in stuck_reserved:
                    # Check if there's ANY active equipment task referencing
                    # this robot.
                    has_task = await session.execute(
                        select(EquipmentTask.id).where(
                            EquipmentTask.state.notin_([
                                EquipmentTaskState.COMPLETED,
                            ]),
                            (
                                (EquipmentTask.a42td_robot_id == db_robot.id)
                                | (EquipmentTask.k50h_robot_id == db_robot.id)
                            ),
                        ).limit(1)
                    )
                    if has_task.scalar_one_or_none() is None:
                        # No active task — clear stale reservation.
                        db_robot.reserved = False
                        db_robot.reservation_order_id = None
                        db_robot.reservation_pick_task_id = None
                        db_robot.reservation_station_id = None
                        await session.flush()
                        # Also update the in-memory robot.
                        mem_robot = robot_map.get(str(db_robot.id))
                        if mem_robot is not None:
                            mem_robot.reserved = False  # type: ignore[attr-defined]
                        logger.info(
                            "Cleared stuck reservation on IDLE robot %s",
                            db_robot.name,
                        )

                await session.commit()

            # Publish events OUTSIDE session
            for evt in pending_events:
                await event_bus.publish(evt)

        except Exception:
            logger.exception("_retry_orphaned_tasks failed")

    # ------------------------------------------------------------------
    # Retry CREATED pick tasks (tote lookup retry)
    # ------------------------------------------------------------------

    async def _retry_created_pick_tasks(self) -> None:
        """Find PickTasks stuck in CREATED state and retry tote lookup.

        When an order is allocated but no tote is available at that moment,
        the pick task stays in CREATED state.  This periodic retry re-checks
        for available totes and dispatches the retrieve flow.
        """
        try:
            from src.shared.database import async_session_factory as _asf_retry
            from src.wes.domain.models import PickTask, Order
            from src.wes.domain.enums import PickTaskState as PTS
            from src.ess.domain.models import Tote
            from src.wes.application.pick_task_service import PickTaskService
            from src.shared.event_bus import event_bus
            from sqlalchemy import select

            pending_retrieves = []

            async with _asf_retry() as session:
                # Find CREATED pick tasks (stuck — no tote was found initially)
                result = await session.execute(
                    select(PickTask).where(
                        PickTask.state == PTS.CREATED,
                        PickTask.source_tote_id.is_(None),
                    ).order_by(PickTask.created_at).limit(5)
                )
                stuck_tasks = result.scalars().all()
                if not stuck_tasks:
                    return

                for pt in stuck_tasks:
                    order = await session.get(Order, pt.order_id)
                    if order is None:
                        continue

                    # Look for an available tote matching the SKU,
                    # preferring totes NOT already in use by other active pick tasks.
                    _in_use_ids = select(PickTask.source_tote_id).where(
                        PickTask.source_tote_id.isnot(None),
                        PickTask.state.notin_([PTS.COMPLETED]),
                        PickTask.id != pt.id,
                    )
                    tote_result = await session.execute(
                        select(Tote).where(
                            Tote.sku == order.sku,
                            Tote.quantity > 0,
                            Tote.current_location_id.isnot(None),
                            Tote.id.notin_(_in_use_ids),
                        ).limit(1)
                    )
                    tote = tote_result.scalar_one_or_none()
                    if tote is None:
                        # All totes in use — fall back to any tote
                        tote_result = await session.execute(
                            select(Tote).where(
                                Tote.sku == order.sku,
                                Tote.quantity > 0,
                                Tote.current_location_id.isnot(None),
                            ).limit(1)
                        )
                        tote = tote_result.scalar_one_or_none()
                    if tote is None:
                        continue

                    # Found a tote — transition CREATED → RESERVED → SOURCE_REQUESTED
                    pt.source_tote_id = tote.id
                    await session.flush()

                    pts = PickTaskService(session)
                    await pts.transition_state(pt.id, "reserve")
                    await pts.transition_state(pt.id, "request_source")

                    for evt in pts.collect_events():
                        await event_bus.publish(evt)

                    # Collect retrieve events for publishing after session
                    from src.wes.domain.events import RetrieveSourceTote
                    pending_retrieves.append(RetrieveSourceTote(
                        pick_task_id=pt.id,
                        tote_id=tote.id,
                        source_location_id=tote.current_location_id,
                        station_id=pt.station_id,
                    ))
                    logger.info(
                        "Retry: CREATED pick_task %s → SOURCE_REQUESTED (tote=%s, sku=%s)",
                        pt.id, tote.id, order.sku,
                    )

                await session.commit()

            # Publish retrieve events outside session
            for evt in pending_retrieves:
                await event_bus.publish(evt)

        except Exception:
            logger.exception("_retry_created_pick_tasks failed")

    # ------------------------------------------------------------------
    # Retry SOURCE_REQUESTED pick tasks (handler failure recovery)
    # ------------------------------------------------------------------

    async def _retry_source_requested_tasks(self) -> None:
        """Find PickTasks stuck in SOURCE_REQUESTED with no EquipmentTask.

        This happens when _handle_retrieve_source_tote fails (exception
        causes session rollback, so no EquipmentTask is persisted).
        Re-publishes the RetrieveSourceTote event.
        """
        try:
            from src.shared.database import async_session_factory as _asf_sr
            from src.wes.domain.models import PickTask
            from src.wes.domain.enums import PickTaskState as PTS
            from src.ess.domain.models import EquipmentTask as ET
            from src.ess.domain.enums import EquipmentTaskType as ETT
            from src.shared.event_bus import event_bus
            from sqlalchemy import select, exists

            pending_events: list = []

            async with _asf_sr() as session:
                # Find SOURCE_REQUESTED pick tasks
                result = await session.execute(
                    select(PickTask).where(
                        PickTask.state == PTS.SOURCE_REQUESTED,
                        PickTask.source_tote_id.isnot(None),
                    ).order_by(PickTask.created_at).limit(5)
                )
                stuck = result.scalars().all()

                for pt in stuck:
                    # Check if a RETRIEVE EquipmentTask exists for this pick_task
                    eq_exists = await session.execute(
                        select(ET.id).where(
                            ET.pick_task_id == pt.id,
                            ET.type == ETT.RETRIEVE,
                        ).limit(1)
                    )
                    if eq_exists.scalar_one_or_none() is not None:
                        continue  # EquipmentTask exists — _retry_orphaned_tasks handles it

                    # No EquipmentTask — need to get tote location for re-dispatch
                    from src.ess.domain.models import Tote
                    tote = await session.get(Tote, pt.source_tote_id)
                    if tote is None or tote.current_location_id is None:
                        continue

                    from src.wes.domain.events import RetrieveSourceTote
                    pending_events.append(RetrieveSourceTote(
                        pick_task_id=pt.id,
                        tote_id=pt.source_tote_id,
                        source_location_id=tote.current_location_id,
                        station_id=pt.station_id,
                    ))
                    logger.info(
                        "Retry: SOURCE_REQUESTED pick_task %s has no EquipmentTask — "
                        "re-publishing RetrieveSourceTote (tote=%s)",
                        pt.id, pt.source_tote_id,
                    )

                await session.commit()

            for evt in pending_events:
                await event_bus.publish(evt)

        except Exception:
            logger.exception("_retry_source_requested_tasks failed")

    # ------------------------------------------------------------------
    # Retry ALLOCATED orders with no PickTask
    # ------------------------------------------------------------------

    async def _retry_allocated_orders(self) -> None:
        """Find orders stuck in ALLOCATED with no PickTask and re-dispatch.

        If _handle_order_allocated fails (session rollback), the order stays
        ALLOCATED but no PickTask is created.  This periodic retry re-runs
        the same logic: create PickTask, find tote, transition to
        SOURCE_REQUESTED, publish RetrieveSourceTote.
        """
        try:
            from src.shared.database import async_session_factory as _asf_ao
            from src.wes.domain.models import Order, PickTask
            from src.wes.domain.enums import OrderStatus, PickTaskState as PTS
            from src.ess.domain.models import Tote
            from src.wes.application.pick_task_service import PickTaskService
            from src.shared.event_bus import event_bus
            from sqlalchemy import select, exists

            pending_events: list = []

            async with _asf_ao() as session:
                # Find ALLOCATED orders
                result = await session.execute(
                    select(Order).where(
                        Order.status == OrderStatus.ALLOCATED,
                        Order.station_id.isnot(None),
                    ).order_by(Order.created_at).limit(5)
                )
                allocated_orders = result.scalars().all()

                for order in allocated_orders:
                    # Check if a PickTask already exists for this order
                    pt_exists = await session.execute(
                        select(PickTask.id).where(
                            PickTask.order_id == order.id,
                        ).limit(1)
                    )
                    if pt_exists.scalar_one_or_none() is not None:
                        continue  # PickTask exists — other retries handle it

                    # No PickTask — recreate (same as _handle_order_allocated)
                    pts = PickTaskService(session)
                    pick_task = await pts.create_pick_task(
                        order_id=order.id,
                        station_id=order.station_id,
                        sku=order.sku,
                        qty=order.quantity,
                    )

                    # Find a tote for this SKU, preferring unused totes
                    from src.wes.domain.enums import PickTaskState as _PTS_ao
                    _in_use_ids_ao = select(PickTask.source_tote_id).where(
                        PickTask.source_tote_id.isnot(None),
                        PickTask.state.notin_([_PTS_ao.COMPLETED]),
                    )
                    tote_result = await session.execute(
                        select(Tote).where(
                            Tote.sku == order.sku,
                            Tote.quantity > 0,
                            Tote.current_location_id.isnot(None),
                            Tote.id.notin_(_in_use_ids_ao),
                        ).limit(1)
                    )
                    tote = tote_result.scalar_one_or_none()
                    if tote is None:
                        # All totes in use — fall back to any tote
                        tote_result = await session.execute(
                            select(Tote).where(
                                Tote.sku == order.sku,
                                Tote.quantity > 0,
                                Tote.current_location_id.isnot(None),
                            ).limit(1)
                        )
                        tote = tote_result.scalar_one_or_none()

                    if tote is not None:
                        pick_task.source_tote_id = tote.id
                        await session.flush()

                        await pts.transition_state(pick_task.id, "reserve")
                        await pts.transition_state(pick_task.id, "request_source")

                        for evt in pts.collect_events():
                            await event_bus.publish(evt)

                        from src.wes.domain.events import RetrieveSourceTote
                        pending_events.append(RetrieveSourceTote(
                            pick_task_id=pick_task.id,
                            tote_id=tote.id,
                            source_location_id=tote.current_location_id,
                            station_id=order.station_id,
                        ))
                        logger.info(
                            "Retry: ALLOCATED order %s → PickTask + SOURCE_REQUESTED "
                            "(tote=%s, sku=%s)",
                            order.external_id, tote.id, order.sku,
                        )
                    else:
                        await session.flush()
                        logger.info(
                            "Retry: ALLOCATED order %s → PickTask CREATED "
                            "(no tote for %s yet)",
                            order.external_id, order.sku,
                        )

                await session.commit()

            for evt in pending_events:
                await event_bus.publish(evt)

        except Exception:
            logger.exception("_retry_allocated_orders failed")

    # ------------------------------------------------------------------
    # Queue rerouting
    # ------------------------------------------------------------------

    async def _reroute_queue_robot(
        self, robot, station_id: uuid.UUID,
    ) -> None:
        """After queue advance, re-plan path to the robot's new target cell."""
        from src.shared.database import async_session_factory as _asf_rq
        from src.wes.application.station_queue_service import StationQueueService as _QSvc2

        target = None
        async with _asf_rq() as _sess:
            qsvc = _QSvc2(_sess)
            target = await qsvc.get_robot_target_cell(station_id, robot.id)

        if target is None:
            logger.warning(
                "Queue reroute: %s has no target cell for station %s (not in queue?)",
                robot.name, station_id,
            )
            return

        if target == (robot.grid_row, robot.grid_col):
            # Already at correct cell — mark as WAITING in queue.
            if robot.status != RobotStatus.WAITING:
                robot.status = RobotStatus.WAITING
                robot._at_queue_cell = True  # type: ignore[attr-defined]
                await self._cache.update_status(robot.id, RobotStatus.WAITING.value)
                logger.info(
                    "Queue reroute: %s already at target %s — set WAITING",
                    robot.name, target,
                )
            return

        # Use _make_planner to include territory constraints for A42TD.
        planner = self._make_planner(robot=robot)
        path = planner.find_path((robot.grid_row, robot.grid_col), target)
        if path and len(path) > 1:
            await self._cache.set_path(robot.id, path[1:])
            robot.status = RobotStatus.MOVING
            robot._at_queue_cell = False  # type: ignore[attr-defined]
            await self._cache.update_status(robot.id, RobotStatus.MOVING.value)
            logger.info(
                "Queue advance: robot %s rerouted to %s (%d steps)",
                robot.name, target, len(path) - 1,
            )
        else:
            logger.warning(
                "Queue reroute: pathfinding failed for %s from (%d,%d) → %s",
                robot.name, robot.grid_row, robot.grid_col, target,
            )

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
