"""Equipment coordination event handlers (RetrieveSourceTote, ReturnSourceTote)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
    find_nearest_rack_edge,
    get_robot_position,
    handler_session,
    plan_and_store_path,
    safe_handler,
)
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


def register(bus: EventBus) -> None:
    from src.wes.domain.events import RetrieveSourceTote, ReturnSourceTote

    bus.subscribe(RetrieveSourceTote, _handle_retrieve_source_tote)
    bus.subscribe(ReturnSourceTote, _handle_return_source_tote)


@safe_handler
async def _handle_retrieve_source_tote(event) -> None:
    """RetrieveSourceTote -> TaskExecutor.execute_retrieve() -> A* path -> Redis."""
    logger.info(
        "RetrieveSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    async with handler_session() as session:
        from src.ess.domain.models import Location
        import src.shared.simulation_state as simulation_state

        svc = HandlerServices(session)
        eq_task = await svc.executor.execute_retrieve(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            source_location_id=event.source_location_id,
            station_id=event.station_id,
        )

        if eq_task.a42td_robot_id is not None and simulation_state.grid:
            robot = await svc.fm.get_robot(eq_task.a42td_robot_id)
            # Use Redis position (simulation state) if available.
            pos = await get_robot_position(robot.id)
            start = pos or (robot.grid_row, robot.grid_col)

            # Plan A42TD path directly to the rack-edge (cantilever handoff
            # point) near the SOURCE column, not the robot's current
            # position.  This distributes robots across different rack-edge
            # cells and avoids congestion at a single target cell.
            source_loc = await session.get(Location, event.source_location_id)
            ref_row = source_loc.grid_row if source_loc else start[0]
            ref_col = source_loc.grid_col if source_loc else start[1]

            # Collect rack-edge cells already targeted by other active A42TDs.
            from src.ess.infrastructure.redis_cache import RobotStateCache
            from src.shared.redis import get_redis
            from src.ess.domain.models import EquipmentTask as _ET
            from src.ess.domain.enums import EquipmentTaskState as _ETS, EquipmentTaskType as _ETT
            from sqlalchemy import select as _sel
            _active = await session.execute(
                _sel(_ET).where(
                    _ET.type == _ETT.RETRIEVE,
                    _ET.a42td_robot_id.isnot(None),
                    _ET.state.in_([_ETS.PENDING, _ETS.A42TD_MOVING]),
                    _ET.id != eq_task.id,
                )
            )
            avoid: set[tuple[int, int]] = set()
            for other in _active.scalars().all():
                if other.source_location_id:
                    other_loc = await session.get(Location, other.source_location_id)
                    if other_loc:
                        other_re = find_nearest_rack_edge(
                            simulation_state.grid, other_loc.grid_row, other_loc.grid_col,
                        )
                        if other_re:
                            avoid.add(other_re)

            rack_edge = find_nearest_rack_edge(
                simulation_state.grid, ref_row, ref_col, avoid_cells=avoid,
            )
            # If all nearby cells are taken, fall back to any rack-edge cell.
            if rack_edge is None:
                rack_edge = find_nearest_rack_edge(
                    simulation_state.grid, ref_row, ref_col,
                )
            planned = None
            if rack_edge is not None:
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    rack_edge,
                )
            elif source_loc is not None:
                # Fallback: use the source location directly (legacy path).
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    (source_loc.grid_row, source_loc.grid_col),
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

            # If A42TD is already at the target rack-edge, fire arrival
            # immediately — the simulation tick won't detect it.
            target = rack_edge or (source_loc.grid_row, source_loc.grid_col) if source_loc else None
            if not planned and target and start == target:
                from src.shared.event_bus import event_bus as _eb
                from src.ess.domain.events import SourceAtCantilever
                from src.wes.domain.models import PickTask as _PT
                _pt = await session.get(_PT, event.pick_task_id)
                if _pt and _pt.source_tote_id:
                    await _eb.publish(SourceAtCantilever(
                        pick_task_id=event.pick_task_id,
                        tote_id=_pt.source_tote_id,
                    ))
                logger.info(
                    "A42TD %s already at rack-edge %s — instant arrival",
                    robot.id, target,
                )

        await session.commit()


@safe_handler
async def _handle_return_source_tote(event) -> None:
    """ReturnSourceTote -> release K50H from station -> execute_return -> path -> Redis."""
    logger.info(
        "ReturnSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    async with handler_session() as session:
        from src.ess.domain.models import Location, Robot
        from src.ess.domain.enums import RobotStatus
        from src.wes.domain.models import Station
        from src.wes.application.reservation_service import ReservationService
        from src.wes.application.station_queue_service import StationQueueService
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis
        from sqlalchemy import select
        import src.shared.simulation_state as simulation_state

        # --- Release the K50H that's holding the tote at the station ---
        # It's stuck in WAITING_FOR_STATION; we need to free it for the return trip.
        result = await session.execute(
            select(Robot).where(
                Robot.hold_pick_task_id == event.pick_task_id,
                Robot.hold_at_station == True,  # noqa: E712
            ).limit(1)
        )
        k50h_at_station = result.scalar_one_or_none()

        if k50h_at_station is not None:
            # Clear station-hold state so find_nearest_idle can pick it up
            k50h_at_station.hold_at_station = False
            k50h_at_station.reserved = False
            k50h_at_station.reservation_order_id = None
            k50h_at_station.reservation_pick_task_id = None
            k50h_at_station.reservation_station_id = None
            k50h_at_station.status = RobotStatus.IDLE
            await session.flush()

            # Update Redis to match
            try:
                redis_client = await get_redis()
                cache = RobotStateCache(redis_client)
                await cache.update_status(k50h_at_station.id, RobotStatus.IDLE.value)
                await cache.clear_reservation(k50h_at_station.id)
                await cache.update_tote_possession(
                    k50h_at_station.id,
                    hold_pick_task_id=event.pick_task_id,
                    hold_at_station=False,
                )
            except Exception:
                logger.debug("Redis sync failed during K50H release (non-critical)")

            # Release station queue slot
            qsvc = StationQueueService(session)
            await qsvc.release_station(event.station_id, k50h_at_station.id)
            await session.flush()

            logger.info(
                "Released K50H %s from WAITING_FOR_STATION for return flow",
                k50h_at_station.id,
            )
        else:
            # K50H not found with hold_at_station=True (simulator may have
            # cleared it already). Still clean up station.current_robot_id
            # to prevent stale references allowing scans without a robot.
            station = await session.get(Station, event.station_id)
            if station is not None and station.current_robot_id is not None:
                qsvc = StationQueueService(session)
                await qsvc.release_station(event.station_id, station.current_robot_id)
                await session.flush()
                logger.info(
                    "Cleaned up stale station.current_robot_id at station %s",
                    event.station_id,
                )

        # Guard: skip if a RETURN equipment task already exists for this pick task
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        existing = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "RETURN task already exists for pick_task %s — skipping",
                event.pick_task_id,
            )
            await session.commit()
            return

        svc = HandlerServices(session)
        eq_task = await svc.executor.execute_return(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            target_location_id=event.target_location_id,
            station_id=event.station_id,
        )

        if eq_task.k50h_robot_id is not None and simulation_state.grid:
            robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
            # Use Redis position (simulation state) if available.
            pos = await get_robot_position(robot.id)
            start = pos or (robot.grid_row, robot.grid_col)

            # K50H goes from station to rack-edge (handoff point, not deep rack).
            cant = find_nearest_rack_edge(simulation_state.grid, start[0], start[1])
            planned = None
            if cant is not None:
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    cant,
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

            # If K50H is already at rack-edge, fire ReturnAtCantilever
            # immediately — the simulation tick won't detect it.
            if not planned and cant and start == cant:
                from src.shared.event_bus import event_bus as _eb
                from src.ess.domain.events import ReturnAtCantilever
                await _eb.publish(ReturnAtCantilever(
                    pick_task_id=event.pick_task_id,
                    tote_id=event.tote_id,
                ))
                logger.info(
                    "K50H %s already at rack-edge %s — instant return arrival",
                    robot.id, cant,
                )

        await session.commit()
