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

            source_loc = await session.get(Location, event.source_location_id)
            if source_loc is not None:
                await plan_and_store_path(
                    svc, robot.id,
                    start,
                    (source_loc.grid_row, source_loc.grid_col),
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

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
            if cant is not None:
                await plan_and_store_path(
                    svc, robot.id,
                    start,
                    cant,
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

        await session.commit()
