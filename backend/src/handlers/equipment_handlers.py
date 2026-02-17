"""Equipment coordination event handlers (RetrieveSourceTote, ReturnSourceTote)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
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
            source_loc = await session.get(Location, event.source_location_id)
            if source_loc is not None:
                await plan_and_store_path(
                    svc, robot.id,
                    (robot.grid_row, robot.grid_col),
                    (source_loc.grid_row, source_loc.grid_col),
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

        await session.commit()


@safe_handler
async def _handle_return_source_tote(event) -> None:
    """ReturnSourceTote -> TaskExecutor.execute_return() -> A* path -> Redis."""
    logger.info(
        "ReturnSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    async with handler_session() as session:
        from src.ess.domain.models import Location
        from src.wes.domain.models import Station
        import src.shared.simulation_state as simulation_state

        svc = HandlerServices(session)
        eq_task = await svc.executor.execute_return(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            target_location_id=event.target_location_id,
            station_id=event.station_id,
        )

        if eq_task.k50h_robot_id is not None and simulation_state.grid:
            robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
            station = await session.get(Station, event.station_id)
            if station is not None:
                target_loc = await session.get(Location, event.target_location_id)
                if target_loc is not None:
                    await plan_and_store_path(
                        svc, robot.id,
                        (robot.grid_row, robot.grid_col),
                        (target_loc.grid_row, target_loc.grid_col),
                    )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

        await session.commit()
