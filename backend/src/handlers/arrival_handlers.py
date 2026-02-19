"""ESS arrival event handlers (robot waypoint arrivals)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
    find_nearest_rack_edge,
    get_robot_position,
    handler_session,
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

    # Build query for tasks that need this robot type
    if robot_type_str == "A42TD":
        # A42TD needed: RETRIEVE first leg (PENDING) or RETURN second leg (AT_CANTILEVER)
        stmt = select(EquipmentTask).where(
            EquipmentTask.a42td_robot_id.is_(None),
            or_(
                and_(
                    EquipmentTask.state == EquipmentTaskState.PENDING,
                    EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                ),
                and_(
                    EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
                    EquipmentTask.type == EquipmentTaskType.RETURN,
                ),
            ),
        ).order_by(EquipmentTask.created_at).limit(1)
    else:
        # K50H needed: RETURN first leg (PENDING) or RETRIEVE second leg (AT_CANTILEVER)
        stmt = select(EquipmentTask).where(
            EquipmentTask.k50h_robot_id.is_(None),
            or_(
                and_(
                    EquipmentTask.state == EquipmentTaskState.PENDING,
                    EquipmentTask.type == EquipmentTaskType.RETURN,
                ),
                and_(
                    EquipmentTask.state == EquipmentTaskState.AT_CANTILEVER,
                    EquipmentTask.type == EquipmentTaskType.RETRIEVE,
                ),
            ),
        ).order_by(EquipmentTask.created_at).limit(1)

    result = await session.execute(stmt)
    eq_task = result.scalar_one_or_none()
    if eq_task is None:
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
        if eq_task.type == EquipmentTaskType.RETRIEVE and eq_task.source_location_id:
            # A42TD → source rack location
            loc = await session.get(Location, eq_task.source_location_id)
            if loc:
                await plan_and_store_path(svc, robot.id, start, (loc.grid_row, loc.grid_col))
        elif eq_task.type == EquipmentTaskType.RETURN:
            # K50H → nearest cantilever
            cant = find_nearest_rack_edge(simulation_state.grid, start[0], start[1])
            if cant:
                await plan_and_store_path(svc, robot.id, start, cant)
        await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

    elif eq_task.state == EquipmentTaskState.AT_CANTILEVER:
        # Second leg dispatch
        if eq_task.type == EquipmentTaskType.RETRIEVE:
            # K50H → station
            pick_task = await session.get(PickTask, eq_task.pick_task_id)
            if pick_task:
                station = await session.get(Station, pick_task.station_id)
                if station:
                    await plan_and_store_path(svc, robot.id, start, (station.grid_row, station.grid_col))
        elif eq_task.type == EquipmentTaskType.RETURN and eq_task.target_location_id:
            # A42TD → target rack location
            loc = await session.get(Location, eq_task.target_location_id)
            if loc:
                await plan_and_store_path(svc, robot.id, start, (loc.grid_row, loc.grid_col))
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
                pos = await get_robot_position(eq_task.a42td_robot_id)
                await svc.fm.release_robot(
                    eq_task.a42td_robot_id, eq_task.id, position=pos,
                )
                await _retry_pending_tasks(session, eq_task.a42td_robot_id, "A42TD")

            # If K50H wasn't assigned at creation, try to find one now.
            if eq_task.k50h_robot_id is None:
                from src.ess.domain.enums import RobotType
                from src.ess.domain.models import Location
                source_loc = await session.get(Location, eq_task.source_location_id) if eq_task.source_location_id else None
                zone_id = source_loc.zone_id if source_loc else None
                if zone_id is not None:
                    k50h = await svc.fm.find_nearest_idle(
                        zone_id=zone_id,
                        robot_type=RobotType.K50H,
                        target_row=source_loc.grid_row if source_loc else 0,
                        target_col=source_loc.grid_col if source_loc else 0,
                    )
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

                # Enter station queue
                qsvc = StationQueueService(session)
                await qsvc.enter_queue(event.station_id, eq_task.k50h_robot_id)

                # Do NOT release K50H or retry pending - it stays at station

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
                pos = await get_robot_position(eq_task.k50h_robot_id)
                await svc.fm.release_robot(
                    eq_task.k50h_robot_id, eq_task.id, position=pos,
                )
                await _retry_pending_tasks(session, eq_task.k50h_robot_id, "K50H")

            # If A42TD wasn't assigned at creation, try to find one now.
            if eq_task.a42td_robot_id is None and eq_task.target_location_id is not None:
                from src.ess.domain.enums import RobotType
                target_loc = await session.get(Location, eq_task.target_location_id)
                if target_loc is not None:
                    a42td = await svc.fm.find_nearest_idle(
                        zone_id=target_loc.zone_id,
                        robot_type=RobotType.A42TD,
                        target_row=target_loc.grid_row,
                        target_col=target_loc.grid_col,
                    )
                    if a42td is not None:
                        await svc.fm.assign_robot(a42td.id, eq_task.id)
                        eq_task.a42td_robot_id = a42td.id
                        await session.flush()

            if eq_task.a42td_robot_id is not None and eq_task.target_location_id is not None:
                robot = await svc.fm.get_robot(eq_task.a42td_robot_id)
                pos = await get_robot_position(robot.id)
                start = pos or (robot.grid_row, robot.grid_col)

                target_loc = await session.get(Location, eq_task.target_location_id)
                if target_loc is not None and simulation_state.grid:
                    await plan_and_store_path(
                        svc, robot.id,
                        start,
                        (target_loc.grid_row, target_loc.grid_col),
                    )

                await svc.executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

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
                pass  # May already be in correct state

            if eq_task.a42td_robot_id is not None:
                pos = await get_robot_position(eq_task.a42td_robot_id)
                await svc.fm.release_robot(
                    eq_task.a42td_robot_id, eq_task.id, position=pos,
                )
                await _retry_pending_tasks(session, eq_task.a42td_robot_id, "A42TD")

        pick_task = await session.get(PickTask, event.pick_task_id)
        if pick_task is not None:
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
