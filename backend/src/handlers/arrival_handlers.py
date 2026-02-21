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
            # A42TD → rack-edge near SOURCE column, avoiding cells
            # already targeted by other active A42TDs.
            loc = await session.get(Location, eq_task.source_location_id)
            ref_row = loc.grid_row if loc else start[0]
            ref_col = loc.grid_col if loc else start[1]
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
                planned = await plan_and_store_path(svc, robot.id, start, rack_edge)
                if not planned and start == rack_edge:
                    already_at_target = True
                elif not planned:
                    # Path planning failed — release robot, keep task PENDING
                    logger.warning(
                        "Retry: path planning failed %s -> %s, releasing %s %s",
                        start, rack_edge, robot_type_str, robot_id,
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
            # K50H → station (set reservation + tote possession first)
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
                await rsvc.set_tote_possession(
                    robot_id,
                    pick_task_id=eq_task.pick_task_id,
                    at_station=False,
                )
                station = await session.get(Station, pick_task.station_id)
                if station:
                    await plan_and_store_path(svc, robot.id, start, (station.grid_row, station.grid_col))
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
                    edge_row = simulation_state.rack_edge_row or (source_loc.grid_row if source_loc else 0)
                    k50h = await svc.fm.find_nearest_idle(
                        zone_id=zone_id,
                        robot_type=RobotType.K50H,
                        target_row=edge_row,
                        target_col=source_loc.grid_col if source_loc else 0,
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

            # Set tote possession + reservation on K50H (picking up tote at cantilever)
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
                await rsvc.set_tote_possession(
                    eq_task.k50h_robot_id,
                    pick_task_id=eq_task.pick_task_id,
                    at_station=False,
                )

            if eq_task.k50h_robot_id is not None and simulation_state.grid:
                robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
                pos = await get_robot_position(robot.id)
                start = pos or (robot.grid_row, robot.grid_col)

                pick_task = await session.get(PickTask, event.pick_task_id)
                if pick_task is not None:
                    station = await session.get(Station, pick_task.station_id)
                    if station is not None:
                        await plan_and_store_path(
                            svc, robot.id,
                            start,
                            (station.grid_row, station.grid_col),
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

                # Enter station queue and directly set current_robot_id
                # (StationQueueService.enter_queue only places in "holding"
                #  but doesn't advance, so we set current_robot_id explicitly.)
                from src.wes.domain.models import Station
                qsvc = StationQueueService(session)
                await qsvc.enter_queue(event.station_id, eq_task.k50h_robot_id)

                station = await session.get(Station, event.station_id)
                if station is not None:
                    station.current_robot_id = eq_task.k50h_robot_id
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
            # Auto-clear the put-wall slot on completion
            if pick_task.put_wall_slot_id:
                from src.wes.domain.models import PutWallSlot
                slot = await session.get(PutWallSlot, pick_task.put_wall_slot_id)
                if slot is not None:
                    slot.target_tote_id = None
                    slot.target_tote_barcode = None
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
