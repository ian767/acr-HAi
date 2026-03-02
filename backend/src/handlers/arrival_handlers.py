"""ESS arrival event handlers (robot waypoint arrivals)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
    find_nearest_rack_edge,
    get_robot_position,
    handler_session,
    is_tote_in_use,
    plan_and_store_path,
    safe_handler,
    ws_broadcast,
)
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


async def _retry_pending_tasks(session, robot_id, robot_type_str: str) -> None:
    """After releasing a robot, try to assign it to the oldest waiting EquipmentTask.

    Searches for tasks where the given robot type is unassigned and the task
    is blocked at PENDING (first leg) or AT_CANTILEVER (second leg).
    """
    from sqlalchemy import select, or_, and_
    from src.ess.domain.models import EquipmentTask, Location
    from src.ess.domain.enums import EquipmentTaskState, EquipmentTaskType
    from src.wes.domain.models import PickTask, Station
    import src.shared.simulation_state as simulation_state

    if not simulation_state.grid:
        return

    # Build query for tasks that need this robot type.
    # PRIORITY: AT_CANTILEVER (second-leg) tasks go first — completing them
    # frees station slots and pick tasks, unblocking the whole pipeline.
    # RETURN AT_CANTILEVER is especially critical because it completes
    # instantly (no physical A42TD movement), freeing the A42TD immediately
    # for the next RETRIEVE.
    if robot_type_str == "A42TD":
        # 1st priority: RETURN AT_CANTILEVER (instant completion)
        stmt_priority = select(EquipmentTask).where(
            EquipmentTask.a42td_robot_id.is_(None),
            EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
            EquipmentTask.type == EquipmentTaskType.RETURN,
        ).order_by(EquipmentTask.created_at).limit(1)
        # 2nd priority: RETRIEVE PENDING (first leg dispatch)
        # Use limit(10) so we can skip tote-in-use candidates.
        stmt_fallback = select(EquipmentTask).where(
            EquipmentTask.a42td_robot_id.is_(None),
            EquipmentTask.state == EquipmentTaskState.PENDING,
            EquipmentTask.type == EquipmentTaskType.RETRIEVE,
        ).order_by(EquipmentTask.created_at).limit(10)
    else:
        # 1st priority: RETRIEVE AT_CANTILEVER (K50H → station)
        stmt_priority = select(EquipmentTask).where(
            EquipmentTask.k50h_robot_id.is_(None),
            EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
            EquipmentTask.type == EquipmentTaskType.RETRIEVE,
        ).order_by(EquipmentTask.created_at).limit(1)
        # 2nd priority: RETURN PENDING (K50H → cantilever)
        stmt_fallback = select(EquipmentTask).where(
            EquipmentTask.k50h_robot_id.is_(None),
            EquipmentTask.state == EquipmentTaskState.PENDING,
            EquipmentTask.type == EquipmentTaskType.RETURN,
        ).order_by(EquipmentTask.created_at).limit(1)

    # Guard: don't reassign a robot that still has active reservations
    from src.ess.domain.models import Robot
    robot = await session.get(Robot, robot_id)
    if robot is not None and (robot.reserved or robot.hold_at_station):
        logger.info(
            "Retry skipped: robot %s still reserved (reserved=%s, hold=%s, station=%s)",
            robot_id, robot.reserved, robot.hold_at_station, robot.reservation_station_id,
        )
        return

    # Guard: don't reassign a robot that is in a station queue
    from src.wes.application.station_queue_service import is_robot_in_any_queue
    if robot is not None and is_robot_in_any_queue(robot_id):
        logger.info("Retry skipped: robot %s is in a station queue", robot_id)
        return

    # Try priority query first, then fallback.
    result = await session.execute(stmt_priority)
    eq_task = result.scalar_one_or_none()
    if eq_task is None:
        result = await session.execute(stmt_fallback)
        eq_task = None
        for candidate in result.scalars().all():
            # For A42TD RETRIEVE: skip if the source tote is currently
            # being processed by another equipment task.
            if (
                robot_type_str == "A42TD"
                and candidate.type == EquipmentTaskType.RETRIEVE
                and await is_tote_in_use(session, candidate.pick_task_id)
            ):
                logger.info(
                    "Retry: tote in use for eq_task %s — skipping",
                    candidate.id,
                )
                continue
            eq_task = candidate
            break

    # If no equipment task found but K50H just released, check for orphaned
    # RETURN_REQUESTED pick tasks that never got a RETURN equipment task.
    if eq_task is None and robot_type_str == "K50H":
        from src.wes.domain.enums import PickTaskState as PTS
        from src.shared.event_bus import event_bus

        orphan_result = await session.execute(
            select(PickTask).where(
                PickTask.state == PTS.RETURN_REQUESTED,
                PickTask.source_tote_id.isnot(None),
            ).order_by(PickTask.created_at).limit(1)
        )
        orphan_pt = orphan_result.scalar_one_or_none()
        if orphan_pt is not None:
            # Check no RETURN equipment task exists
            existing_return = await session.execute(
                select(EquipmentTask).where(
                    EquipmentTask.pick_task_id == orphan_pt.id,
                    EquipmentTask.type == EquipmentTaskType.RETURN,
                ).limit(1)
            )
            if existing_return.scalar_one_or_none() is None:
                from src.ess.domain.models import Tote as ToteModel
                src_tote = await session.get(ToteModel, orphan_pt.source_tote_id)
                if src_tote and src_tote.home_location_id:
                    from src.wes.domain.events import ReturnSourceTote
                    logger.info(
                        "Triggering orphaned return for pick_task %s (K50H %s available)",
                        orphan_pt.id, robot_id,
                    )
                    await event_bus.publish(ReturnSourceTote(
                        pick_task_id=orphan_pt.id,
                        tote_id=orphan_pt.source_tote_id,
                        target_location_id=src_tote.home_location_id,
                        station_id=orphan_pt.station_id,
                    ))
                    return  # ReturnSourceTote handler will assign this K50H

    if eq_task is None:
        logger.info(
            "Retry: no pending task found for %s %s",
            robot_type_str, robot_id,
        )
        return

    # Assign the just-released robot
    svc = HandlerServices(session)
    await svc.fm.assign_robot(robot_id, eq_task.id)
    if robot_type_str == "A42TD":
        eq_task.a42td_robot_id = robot_id
    else:
        eq_task.k50h_robot_id = robot_id
    await session.flush()

    # Plan path based on current task state
    robot = await svc.fm.get_robot(robot_id)
    pos = await get_robot_position(robot.id)
    start = pos or (robot.grid_row, robot.grid_col)

    if eq_task.state == EquipmentTaskState.PENDING:
        # First leg dispatch
        already_at_target = False
        planned = None
        if eq_task.type == EquipmentTaskType.RETRIEVE and eq_task.source_location_id:
            # A42TD two-leg path: start → source rack → cantilever.
            # Leg 1: move to the FLOOR cell adjacent to the source rack
            # Leg 2: carry tote from rack to cantilever (rack-edge row)
            loc = await session.get(Location, eq_task.source_location_id)
            ref_row = loc.grid_row if loc else start[0]
            ref_col = loc.grid_col if loc else start[1]

            # Find the source rack-adjacent cell (nearest walkable to rack)
            from src.ess.application.path_planner import PathPlanner as _PP_a42
            _a42_tcols = None
            _a42_trows = None
            if getattr(robot, "territory_col_min", None) is not None:
                _a42_tcols = (robot.territory_col_min, robot.territory_col_max)
            if getattr(robot, "territory_row_min", None) is not None:
                _a42_trows = (robot.territory_row_min, robot.territory_row_max)
            _planner_a42 = _PP_a42(
                simulation_state.grid or [], aisle_rows=simulation_state.aisle_rows,
                territory_cols=_a42_tcols, territory_rows=_a42_trows,
            )
            source_cell = _planner_a42._nearest_walkable((ref_row, ref_col))
            if source_cell is None:
                source_cell = (ref_row, ref_col)

            # Cantilever target: rack-edge near source column
            from sqlalchemy import select as _sel2
            _active2 = await session.execute(
                _sel2(EquipmentTask).where(
                    EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                    EquipmentTask.a42td_robot_id.isnot(None),
                    EquipmentTask.state.in_([EquipmentTaskState.PENDING, EquipmentTaskState.A42TD_MOVING]),
                    EquipmentTask.id != eq_task.id,
                )
            )
            avoid2: set[tuple[int, int]] = set()
            for other in _active2.scalars().all():
                if other.source_location_id:
                    other_loc = await session.get(Location, other.source_location_id)
                    if other_loc:
                        other_re = find_nearest_rack_edge(
                            simulation_state.grid, other_loc.grid_row, other_loc.grid_col,
                        )
                        if other_re:
                            avoid2.add(other_re)
            rack_edge = find_nearest_rack_edge(
                simulation_state.grid, ref_row, ref_col, avoid_cells=avoid2,
            )
            if rack_edge is None:
                rack_edge = find_nearest_rack_edge(simulation_state.grid, ref_row, ref_col)
            if rack_edge:
                # Plan two-segment path: start → source rack → cantilever
                seg1 = _planner_a42.find_path(start, source_cell)
                seg2 = _planner_a42.find_path(source_cell, rack_edge)
                if seg1 and seg2:
                    combined = seg1[1:] + seg2[1:]
                    from src.ess.infrastructure.redis_cache import RobotStateCache as _RSC_a42
                    from src.shared.redis import get_redis as _gr_a42
                    _r_a42 = await _gr_a42()
                    _c_a42 = _RSC_a42(_r_a42)
                    await _c_a42.set_path(robot.id, combined)
                    planned = combined
                    logger.info(
                        "A42TD %s: %s → source %s → cantilever %s (%d steps)",
                        robot.id, start, source_cell, rack_edge, len(combined),
                    )
                elif start == rack_edge or start == source_cell:
                    already_at_target = True
                else:
                    # Fallback: direct path to cantilever (with territory)
                    from src.ess.domain.enums import RobotType as _RT_a42fb
                    planned = await plan_and_store_path(
                        svc, robot.id, start, rack_edge,
                        robot_type=_RT_a42fb(robot_type_str),
                        territory_cols=_a42_tcols, territory_rows=_a42_trows,
                    )
                if not planned and not already_at_target:
                    logger.warning(
                        "Retry: path planning failed %s -> %s -> %s, releasing %s %s",
                        start, source_cell, rack_edge, robot_type_str, robot_id,
                    )
                    await svc.fm.release_robot(robot_id, eq_task.id)
                    if robot_type_str == "A42TD":
                        eq_task.a42td_robot_id = None
                    else:
                        eq_task.k50h_robot_id = None
                    await session.flush()
                    return
        elif eq_task.type == EquipmentTaskType.RETURN:
            # K50H → nearest cantilever
            cant = find_nearest_rack_edge(simulation_state.grid, start[0], start[1])
            if cant:
                planned = await plan_and_store_path(svc, robot.id, start, cant)
                if not planned and start == cant:
                    already_at_target = True
                elif not planned:
                    logger.warning(
                        "Retry: path planning failed %s -> %s, releasing K50H %s",
                        start, cant, robot_id,
                    )
                    await svc.fm.release_robot(robot_id, eq_task.id)
                    eq_task.k50h_robot_id = None
                    await session.flush()
                    return
        await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

        # If robot is already at the target, the simulation tick won't detect
        # an "arrival" because it didn't move.  Fire the event immediately.
        if already_at_target and eq_task.type == EquipmentTaskType.RETRIEVE:
            from src.shared.event_bus import event_bus as _eb_instant
            from src.ess.domain.events import SourceAtCantilever
            from src.wes.domain.models import PickTask as _PT_instant
            _pt_inst = await session.get(_PT_instant, eq_task.pick_task_id)
            if _pt_inst and _pt_inst.source_tote_id:
                await _eb_instant.publish(SourceAtCantilever(
                    pick_task_id=eq_task.pick_task_id,
                    tote_id=_pt_inst.source_tote_id,
                ))
            logger.info(
                "Retry: A42TD %s already at rack-edge %s — instant arrival for eq_task %s",
                robot_id, rack_edge, eq_task.id,
            )
            return
        elif already_at_target and eq_task.type == EquipmentTaskType.RETURN:
            from src.shared.event_bus import event_bus as _eb_ret
            from src.ess.domain.events import ReturnAtCantilever
            from src.wes.domain.models import PickTask as _PT_ret
            _pt_ret = await session.get(_PT_ret, eq_task.pick_task_id)
            if _pt_ret and _pt_ret.source_tote_id:
                await _eb_ret.publish(ReturnAtCantilever(
                    pick_task_id=eq_task.pick_task_id,
                    tote_id=_pt_ret.source_tote_id,
                ))
            logger.info(
                "Retry: K50H %s already at rack-edge %s — instant return arrival for eq_task %s",
                robot_id, cant, eq_task.id,
            )
            return

    elif eq_task.state == EquipmentTaskState.AT_CANTILEVER:
        # Second leg dispatch
        if eq_task.type == EquipmentTaskType.RETRIEVE:
            # K50H → station (set reservation; tote possession deferred
            # until K50H physically reaches the cantilever/rack-edge row)
            from src.wes.application.reservation_service import ReservationService
            pick_task = await session.get(PickTask, eq_task.pick_task_id)
            if pick_task:
                rsvc = ReservationService(session)
                await rsvc.create_reservation(
                    robot_id=robot_id,
                    order_id=pick_task.order_id,
                    pick_task_id=eq_task.pick_task_id,
                    station_id=pick_task.station_id,
                )
                station = await session.get(Station, pick_task.station_id)
                if station:
                    # Direct queue slot routing: find next free slot
                    from src.wes.application.station_queue_service import StationQueueService as _QSvc
                    _qsvc = _QSvc(session)
                    _slot_name, _slot_idx, _slot_cell = await _qsvc.find_next_slot(station.id)
                    if _slot_cell is not None:
                        station_pos = _slot_cell
                        # Pre-register in queue so next dispatch sees it
                        await _qsvc.place_in_slot(station.id, robot_id, _slot_name, _slot_idx)
                        logger.info(
                            "K50H %s: station %s → slot %s[%s] at %s",
                            robot.id, station.name, _slot_name, _slot_idx, station_pos,
                        )
                    else:
                        # Queue full — still register via enter_queue (goes to
                        # holding) so the robot is tracked and queue advance
                        # will eventually reroute it.
                        await _qsvc.enter_queue(station.id, robot_id)
                        target = await _qsvc.get_robot_target_cell(station.id, robot_id)
                        if target is not None:
                            station_pos = target
                        elif station.approach_cell_row is not None:
                            station_pos = (station.approach_cell_row, station.approach_cell_col)
                        else:
                            station_pos = (station.grid_row, station.grid_col)
                        logger.warning(
                            "K50H %s: station %s queue full, registered in overflow → %s",
                            robot.id, station.name, station_pos,
                        )
                    # Route K50H through cantilever first (tote handoff point)
                    from src.ess.domain.models import Location as _RetryLoc
                    src_loc = await session.get(_RetryLoc, eq_task.source_location_id) if eq_task.source_location_id else None
                    ref_col = src_loc.grid_col if src_loc else start[1]
                    cant = find_nearest_rack_edge(
                        simulation_state.grid, start[0], ref_col,
                    )
                    if cant and cant != start:
                        from src.ess.application.path_planner import PathPlanner as _PP2
                        from src.ess.domain.enums import RobotType as _RT2
                        _planner = _PP2(simulation_state.grid, robot_type=_RT2.K50H, aisle_rows=simulation_state.aisle_rows)
                        _s1 = _planner.find_path(start, cant)
                        _s2 = _planner.find_path(cant, station_pos)
                        if _s1 and _s2:
                            combined = _s1[1:] + _s2[1:]
                            from src.ess.infrastructure.redis_cache import RobotStateCache as _RSC2
                            from src.shared.redis import get_redis as _gr2
                            _r2 = await _gr2()
                            _c2 = _RSC2(_r2)
                            await _c2.set_path(robot.id, combined)
                        else:
                            await plan_and_store_path(svc, robot.id, start, station_pos, robot_type=_RT2.K50H, avoid_queue=False)
                    else:
                        await plan_and_store_path(svc, robot.id, start, station_pos, robot_type=_RT2.K50H, avoid_queue=False)
        elif eq_task.type == EquipmentTaskType.RETURN:
            # RETURN A42TD: complete immediately (no physical movement).
            # The cantilever is the handoff point — A42TD doesn't need to move.
            await svc.executor.advance_task(eq_task.id, "k50h_dispatched")
            try:
                await svc.executor.advance_task(eq_task.id, "delivered")
                await svc.executor.advance_task(eq_task.id, "completed")
            except ValueError:
                logger.warning("RETURN eq_task %s: advance to delivered/completed skipped (already in target state)", eq_task.id)
            # Release A42TD immediately
            a42td_pos = await get_robot_position(robot_id)
            await svc.fm.release_robot(robot_id, eq_task.id, position=a42td_pos)
            eq_task.a42td_robot_id = None
            await session.flush()
            # Queue SourceBackInRack — event bus is queue-based so it won't
            # be processed until after the current handler completes.
            from src.shared.event_bus import event_bus as _eb
            from src.ess.domain.events import SourceBackInRack
            from src.wes.domain.models import PickTask as _PT
            _pt = await session.get(_PT, eq_task.pick_task_id)
            if _pt is not None and _pt.source_tote_id:
                await _eb.publish(SourceBackInRack(
                    pick_task_id=eq_task.pick_task_id,
                    tote_id=_pt.source_tote_id,
                    location_id=eq_task.target_location_id,
                ))
            logger.info(
                "Retry: RETURN A42TD %s completed immediately for eq_task %s",
                robot_id, eq_task.id,
            )
            # A42TD is free again — recursively check for more waiting tasks.
            await _retry_pending_tasks(session, robot_id, "A42TD")
            return  # Already done; skip the generic advance below
        await svc.executor.advance_task(eq_task.id, "k50h_dispatched")

    logger.info(
        "Retry: assigned %s %s to eq_task %s (state=%s, type=%s)",
        robot_type_str, robot_id, eq_task.id, eq_task.state.value, eq_task.type.value,
    )


def register(bus: EventBus) -> None:
    from src.ess.domain.events import (
        ReturnAtCantilever,
        SourceAtCantilever,
        SourceAtStation,
        SourceBackInRack,
        SourcePicked,
    )

    bus.subscribe(SourceAtCantilever, _handle_source_at_cantilever)
    bus.subscribe(SourcePicked, _handle_source_picked)
    bus.subscribe(SourceAtStation, _handle_source_at_station)
    bus.subscribe(ReturnAtCantilever, _handle_return_at_cantilever)
    bus.subscribe(SourceBackInRack, _handle_source_back_in_rack)


@safe_handler
async def _handle_source_at_cantilever(event) -> None:
    """SourceAtCantilever -> transition PickTask + dispatch K50H."""
    logger.info("SourceAtCantilever: pick_task=%s", event.pick_task_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.wes.application.reservation_service import ReservationService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.wes.domain.models import Station, PickTask
        from src.shared.event_bus import event_bus
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_at_cantilever")

        for evt in pts.collect_events():
            await event_bus.publish(evt)

        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETRIEVE,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()

        if eq_task is not None:
            svc = HandlerServices(session)
            await svc.executor.advance_task(eq_task.id, "at_cantilever")

            # A42TD has completed its RETRIEVE leg (rack→cantilever).
            # Release it immediately so it's available for new tasks.
            if eq_task.a42td_robot_id is not None:
                released_a42td_id = eq_task.a42td_robot_id
                pos = await get_robot_position(released_a42td_id)
                await svc.fm.release_robot(
                    released_a42td_id, eq_task.id, position=pos,
                )
                # Clear robot ref so _emit_arrival_event won't match this
                # task when the A42TD is reassigned to a different task.
                eq_task.a42td_robot_id = None
                await session.flush()
                await _retry_pending_tasks(session, released_a42td_id, "A42TD")

            # Assign K50H now (deferred from execute_retrieve).
            # Search near the rack-edge (cantilever) — the actual handoff point.
            if eq_task.k50h_robot_id is None:
                from src.ess.domain.enums import RobotType
                from src.ess.domain.models import Location
                from src.ess.domain.models import Robot as RobotModel
                from src.wes.domain.enums import PickTaskState as PTS
                source_loc = await session.get(Location, eq_task.source_location_id) if eq_task.source_location_id else None
                zone_id = source_loc.zone_id if source_loc else None
                if zone_id is not None:
                    # Search for K50H near the source tote's position
                    # (per-aisle handoff — tote is at the A42TD's aisle).
                    k50h = await svc.fm.find_nearest_idle(
                        zone_id=zone_id,
                        robot_type=RobotType.K50H,
                        target_row=source_loc.grid_row if source_loc else 0,
                        target_col=source_loc.grid_col if source_loc else 0,
                        aisle_rows=simulation_state.aisle_rows,
                    )

                    # Recovery: if no idle K50H, force-release stuck ones at stations
                    # whose pick task is already done or no longer needs the robot.
                    if k50h is None:
                        stuck_result = await session.execute(
                            select(RobotModel).where(
                                RobotModel.type == RobotType.K50H,
                                RobotModel.hold_at_station == True,  # noqa: E712
                                RobotModel.zone_id == zone_id,
                            )
                        )
                        stuck_robots = stuck_result.scalars().all()
                        for sr in stuck_robots:
                            old_pt = await session.get(PickTask, sr.hold_pick_task_id) if sr.hold_pick_task_id else None
                            # Release if: no pick task, or pick task is done/returning
                            if old_pt is None or old_pt.state in (
                                PTS.RETURN_REQUESTED, PTS.RETURN_AT_CANTILEVER, PTS.COMPLETED,
                            ):
                                logger.warning(
                                    "Force-releasing stuck K50H %s (hold_pick_task=%s, state=%s)",
                                    sr.id, sr.hold_pick_task_id,
                                    old_pt.state.value if old_pt else "N/A",
                                )
                                pos = await get_robot_position(sr.id)
                                await svc.fm.release_robot(sr.id, sr.current_task_id, position=pos)
                                await session.flush()
                                k50h = sr
                                break

                    if k50h is not None:
                        await svc.fm.assign_robot(k50h.id, eq_task.id)
                        eq_task.k50h_robot_id = k50h.id
                        await session.flush()

            # Set reservation on K50H (tote possession deferred until
            # K50H physically reaches the cantilever/rack-edge row).
            if eq_task.k50h_robot_id is not None:
                rsvc = ReservationService(session)
                pick_task = await session.get(PickTask, eq_task.pick_task_id)
                if pick_task is not None:
                    await rsvc.create_reservation(
                        robot_id=eq_task.k50h_robot_id,
                        order_id=pick_task.order_id,
                        pick_task_id=eq_task.pick_task_id,
                        station_id=pick_task.station_id,
                    )

            if eq_task.k50h_robot_id is not None and simulation_state.grid:
                robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
                pos = await get_robot_position(robot.id)
                start = pos or (robot.grid_row, robot.grid_col)

                pick_task = await session.get(PickTask, event.pick_task_id)
                if pick_task is not None:
                    station = await session.get(Station, pick_task.station_id)
                    if station is not None:
                        # Pull-based FIFO: always enter through Qn (last/farthest Q cell)
                        from src.wes.application.station_queue_service import StationQueueService as _QSvc
                        import src.shared.simulation_state as _ss_dispatch
                        _qsvc = _QSvc(session)
                        _qs = _qsvc._get_queue_state(station)

                        # ALWAYS add to pending queue — FIFO pull admission
                        # is the SINGLE entry point to the queue chain.
                        # This eliminates race conditions where two dispatch
                        # paths both target the same Qn cell.
                        _pending = _ss_dispatch.queue_pending.setdefault(str(station.id), [])
                        _rid_str = str(eq_task.k50h_robot_id)
                        if _rid_str not in _pending:
                            _pending.append(_rid_str)
                        logger.info(
                            "K50H %s: station %s → pending queue (pos %d, FIFO admission)",
                            robot.id, station.name, len(_pending),
                        )

                        # Route K50H to the rack-adjacent aisle cell where
                        # the A42TD delivered the tote (per-aisle handoff).
                        # Use the source tote's position to find the
                        # nearest rack-adjacent FLOOR cell.
                        from src.ess.domain.models import Location as _Loc
                        from src.ess.domain.enums import RobotType as _RType
                        src_loc = await session.get(_Loc, eq_task.source_location_id) if eq_task.source_location_id else None
                        ref_row = src_loc.grid_row if src_loc else start[0]
                        ref_col = src_loc.grid_col if src_loc else start[1]
                        cantilever = find_nearest_rack_edge(
                            simulation_state.grid, ref_row, ref_col,
                        )

                        if cantilever and cantilever != start:
                            from src.ess.application.path_planner import PathPlanner as _PP
                            _avoid = _ss_dispatch.queue_area_cells or None
                            planner = _PP(
                                simulation_state.grid, robot_type=_RType.K50H,
                                aisle_rows=simulation_state.aisle_rows,
                                avoid_cells=_avoid,
                            )
                            path = planner.find_path(start, cantilever)
                            if path and len(path) > 1:
                                from src.ess.infrastructure.redis_cache import RobotStateCache as _RSC
                                from src.shared.redis import get_redis as _get_redis
                                _redis = await _get_redis()
                                _cache = _RSC(_redis)
                                await _cache.set_path(robot.id, path[1:])
                                logger.info(
                                    "K50H %s routed to cantilever %s (%d steps)",
                                    robot.id, cantilever, len(path) - 1,
                                )

                await svc.executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_CANTILEVER",
    })
    await ws_broadcast("pickTask.state_changed", {
        "type": "pickTask.state_changed",
        "pickTaskId": str(event.pick_task_id),
        "from": "SOURCE_REQUESTED",
        "to": "SOURCE_AT_CANTILEVER",
    })


@safe_handler
async def _handle_source_picked(event) -> None:
    """SourcePicked -> transition PickTask SOURCE_AT_CANTILEVER -> SOURCE_PICKED."""
    logger.info("SourcePicked: pick_task=%s robot=%s", event.pick_task_id, event.robot_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.shared.event_bus import event_bus

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_picked")

        for evt in pts.collect_events():
            await event_bus.publish(evt)

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_PICKED",
    })
    await ws_broadcast("pickTask.state_changed", {
        "type": "pickTask.state_changed",
        "pickTaskId": str(event.pick_task_id),
        "from": "SOURCE_AT_CANTILEVER",
        "to": "SOURCE_PICKED",
    })


@safe_handler
async def _handle_source_at_station(event) -> None:
    """SourceAtStation -> transition PickTask state, set hold_at_station."""
    logger.info("SourceAtStation: pick_task=%s station=%s", event.pick_task_id, event.station_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.wes.application.reservation_service import ReservationService
        from src.wes.application.station_queue_service import StationQueueService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.shared.event_bus import event_bus
        from sqlalchemy import select

        pts = PickTaskService(session)

        # Catch-up: if intermediate states were skipped (e.g. K50H arrived
        # at station before SourceAtCantilever/SourcePicked were processed),
        # advance through all missing states so the pick task reaches
        # SOURCE_AT_STATION regardless of its current position.
        from src.wes.domain.enums import PickTaskState
        from src.wes.domain.models import PickTask
        pt = await session.get(PickTask, event.pick_task_id)
        if pt is not None:
            catchup_events = {
                PickTaskState.SOURCE_REQUESTED: ["source_at_cantilever", "source_picked", "source_at_station"],
                PickTaskState.SOURCE_AT_CANTILEVER: ["source_picked", "source_at_station"],
                PickTaskState.SOURCE_PICKED: ["source_at_station"],
            }
            events_needed = catchup_events.get(pt.state, [])
            if events_needed:
                if len(events_needed) > 1:
                    logger.info(
                        "SourceAtStation catch-up: pick_task %s in %s, advancing through %s",
                        event.pick_task_id, pt.state.value, events_needed,
                    )
                for ev in events_needed:
                    await pts.transition_state(event.pick_task_id, ev)
            # Already at SOURCE_AT_STATION or beyond — skip transition
        else:
            await pts.transition_state(event.pick_task_id, "source_at_station")

        for evt in pts.collect_events():
            await event_bus.publish(evt)

        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETRIEVE,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            svc = HandlerServices(session)
            await svc.executor.advance_task(eq_task.id, "delivered")
            await svc.executor.advance_task(eq_task.id, "completed")

            # Set hold_at_station=True on K50H (don't release it yet)
            if eq_task.k50h_robot_id is not None:
                rsvc = ReservationService(session)
                await rsvc.set_tote_possession(
                    eq_task.k50h_robot_id,
                    pick_task_id=eq_task.pick_task_id,
                    at_station=True,
                )

                # Register robot at approach and promote to serving (station slot).
                # Do NOT call advance_queue() — its compact logic would shift
                # Q slot assignments without physical movement, breaking FIFO pull.
                from src.wes.domain.models import Station
                qsvc = StationQueueService(session)
                station = await session.get(Station, event.station_id)
                if station is not None:
                    queue_state = qsvc._get_queue_state(station)
                    queue_state["approach"] = str(eq_task.k50h_robot_id)
                    queue_state["station"] = str(eq_task.k50h_robot_id)
                    station.current_robot_id = eq_task.k50h_robot_id
                    qsvc._save_queue_state(station, queue_state, reason="source_at_station")
                    await session.flush()

                # Do NOT release K50H or retry pending - it stays at station

        # Auto-match pick task to a pre-bound putwall slot (tote-first workflow).
        from src.wes.domain.models import PutWallSlot, PickTask as PT
        from src.ess.domain.models import Tote
        pick_task = await session.get(PT, event.pick_task_id)
        if pick_task is not None and pick_task.target_tote_id is None:
            available = await session.execute(
                select(PutWallSlot).where(
                    PutWallSlot.station_id == event.station_id,
                    PutWallSlot.target_tote_id.isnot(None),
                    PutWallSlot.is_locked == False,  # noqa: E712
                ).order_by(PutWallSlot.slot_label).limit(1)
            )
            slot = available.scalar_one_or_none()
            if slot is not None:
                pick_task.target_tote_id = slot.target_tote_id
                pick_task.target_tote_barcode = slot.target_tote_barcode
                pick_task.put_wall_slot_id = slot.id
                slot.is_locked = True
                await session.flush()
                logger.info(
                    "Auto-matched pick_task %s to putwall slot %s (tote=%s)",
                    event.pick_task_id, slot.slot_label, slot.target_tote_barcode,
                )

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_STATION",
    })
    await ws_broadcast("pickTask.state_changed", {
        "type": "pickTask.state_changed",
        "pickTaskId": str(event.pick_task_id),
        "stationId": str(event.station_id),
        "from": "SOURCE_PICKED",
        "to": "SOURCE_AT_STATION",
    })
    await ws_broadcast("station.ready", {
        "type": "station.ready",
        "stationId": str(event.station_id),
        "pickTaskId": str(event.pick_task_id),
    })


@safe_handler
async def _handle_return_at_cantilever(event) -> None:
    """ReturnAtCantilever -> transition PickTask + dispatch A42TD for return leg."""
    logger.info("ReturnAtCantilever: pick_task=%s", event.pick_task_id)

    pending_source_back = None  # Set inside session if A42TD already at rack-edge

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.ess.domain.models import EquipmentTask, Location
        from src.ess.domain.enums import EquipmentTaskType
        from src.shared.event_bus import event_bus
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "return_at_cantilever")

        for evt in pts.collect_events():
            await event_bus.publish(evt)

        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            svc = HandlerServices(session)
            await svc.executor.advance_task(eq_task.id, "at_cantilever")

            # K50H has completed its RETURN leg (station→cantilever).
            # Release it immediately so it's available for new tasks.
            if eq_task.k50h_robot_id is not None:
                released_k50h_id = eq_task.k50h_robot_id
                pos = await get_robot_position(released_k50h_id)
                await svc.fm.release_robot(
                    released_k50h_id, eq_task.id, position=pos,
                )
                # Clear robot ref so _emit_arrival_event won't match this
                # task when the K50H is reassigned to a different task.
                eq_task.k50h_robot_id = None
                await session.flush()
                await _retry_pending_tasks(session, released_k50h_id, "K50H")

            # If A42TD wasn't assigned at creation, try to find one now.
            # Use rack_edge_row (cantilever) as target — the A42TD completes
            # instantly so proximity doesn't matter much, but this is consistent
            # with the actual handoff location.
            if eq_task.a42td_robot_id is None and eq_task.target_location_id is not None:
                from src.ess.domain.enums import RobotType
                target_loc = await session.get(Location, eq_task.target_location_id)
                if target_loc is not None:
                    edge_row = simulation_state.rack_edge_row or target_loc.grid_row
                    a42td = await svc.fm.find_nearest_idle(
                        zone_id=target_loc.zone_id,
                        robot_type=RobotType.A42TD,
                        target_row=edge_row,
                        target_col=target_loc.grid_col,
                        aisle_rows=simulation_state.aisle_rows,
                    )
                    if a42td is not None:
                        await svc.fm.assign_robot(a42td.id, eq_task.id)
                        eq_task.a42td_robot_id = a42td.id
                        await session.flush()

            # RETURN A42TD leg: complete immediately without physical movement.
            # The cantilever (rack_edge) is the same physical handoff point for
            # both RETRIEVE and RETURN.  The A42TD conceptually "returns the tote
            # to the rack" from the cantilever — no actual robot movement needed.
            if eq_task.a42td_robot_id is not None:
                await svc.executor.advance_task(eq_task.id, "k50h_dispatched")
                try:
                    await svc.executor.advance_task(eq_task.id, "delivered")
                    await svc.executor.advance_task(eq_task.id, "completed")
                except ValueError:
                    logger.warning("RETURN eq_task %s: advance to delivered/completed skipped (already in target state)", eq_task.id)
                # Release A42TD
                released_a42td_id = eq_task.a42td_robot_id
                a42td_pos = await get_robot_position(released_a42td_id)
                await svc.fm.release_robot(
                    released_a42td_id, eq_task.id, position=a42td_pos,
                )
                eq_task.a42td_robot_id = None
                await session.flush()
                await _retry_pending_tasks(session, released_a42td_id, "A42TD")
                # Queue SourceBackInRack for pick task completion
                pending_source_back = {
                    "pick_task_id": eq_task.pick_task_id,
                    "tote_id": None,  # filled below
                    "location_id": eq_task.target_location_id,
                }
                from src.wes.domain.models import PickTask as PT2
                pt = await session.get(PT2, eq_task.pick_task_id)
                if pt is not None:
                    pending_source_back["tote_id"] = pt.source_tote_id

        await session.commit()

    # If A42TD was already at rack-edge, complete the return immediately.
    if pending_source_back is not None and pending_source_back.get("tote_id"):
        from src.ess.domain.events import SourceBackInRack
        from src.shared.event_bus import event_bus
        await event_bus.publish(SourceBackInRack(
            pick_task_id=pending_source_back["pick_task_id"],
            tote_id=pending_source_back["tote_id"],
            location_id=pending_source_back["location_id"],
        ))

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "RETURN_AT_CANTILEVER",
    })
    await ws_broadcast("pickTask.state_changed", {
        "type": "pickTask.state_changed",
        "pickTaskId": str(event.pick_task_id),
        "from": "RETURN_REQUESTED",
        "to": "RETURN_AT_CANTILEVER",
    })


@safe_handler
async def _handle_source_back_in_rack(event) -> None:
    """SourceBackInRack -> complete PickTask + check Order completion."""
    logger.info("SourceBackInRack: pick_task=%s", event.pick_task_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.wes.domain.models import PickTask
        from src.wes.domain.enums import PickTaskState
        from src.wes.application.order_service import OrderService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.shared.event_bus import event_bus
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_back_in_rack")

        for evt in pts.collect_events():
            await event_bus.publish(evt)

        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            svc = HandlerServices(session)
            try:
                await svc.executor.advance_task(eq_task.id, "delivered")
                await svc.executor.advance_task(eq_task.id, "completed")
            except ValueError:
                logger.warning("RETURN eq_task %s: advance to delivered/completed skipped (already in target state)", eq_task.id)

            if eq_task.a42td_robot_id is not None:
                released_a42td_id = eq_task.a42td_robot_id
                pos = await get_robot_position(released_a42td_id)
                await svc.fm.release_robot(
                    released_a42td_id, eq_task.id, position=pos,
                )
                eq_task.a42td_robot_id = None
                await session.flush()
                await _retry_pending_tasks(session, released_a42td_id, "A42TD")

        pick_task = await session.get(PickTask, event.pick_task_id)
        if pick_task is not None:
            # Unlink the pick task from the putwall slot and unlock it,
            # but KEEP the tote binding (target_tote_id / barcode).
            # The operator can still manually "Tote Full" to clear it,
            # or re-use the slot for the next pick task.
            if pick_task.put_wall_slot_id:
                from src.wes.domain.models import PutWallSlot
                slot = await session.get(PutWallSlot, pick_task.put_wall_slot_id)
                if slot is not None:
                    slot.is_locked = False
                pick_task.put_wall_slot_id = None

            result = await session.execute(
                select(PickTask).where(
                    PickTask.order_id == pick_task.order_id,
                    PickTask.state != PickTaskState.COMPLETED,
                )
            )
            incomplete = result.scalars().all()
            if not incomplete:
                os = OrderService(session)
                await os.complete_order(pick_task.order_id)
                for evt in os.collect_events():
                    await event_bus.publish(evt)

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "COMPLETED",
    })
    await ws_broadcast("pickTask.state_changed", {
        "type": "pickTask.state_changed",
        "pickTaskId": str(event.pick_task_id),
        "from": "RETURN_AT_CANTILEVER",
        "to": "COMPLETED",
    })
