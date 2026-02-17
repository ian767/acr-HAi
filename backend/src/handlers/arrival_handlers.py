"""ESS arrival event handlers (robot waypoint arrivals)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
    handler_session,
    plan_and_store_path,
    safe_handler,
    ws_broadcast,
)
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


def register(bus: EventBus) -> None:
    from src.ess.domain.events import (
        ReturnAtCantilever,
        SourceAtCantilever,
        SourceAtStation,
        SourceBackInRack,
    )

    bus.subscribe(SourceAtCantilever, _handle_source_at_cantilever)
    bus.subscribe(SourceAtStation, _handle_source_at_station)
    bus.subscribe(ReturnAtCantilever, _handle_return_at_cantilever)
    bus.subscribe(SourceBackInRack, _handle_source_back_in_rack)


@safe_handler
async def _handle_source_at_cantilever(event) -> None:
    """SourceAtCantilever -> transition PickTask + dispatch K50H."""
    logger.info("SourceAtCantilever: pick_task=%s", event.pick_task_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
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

            if eq_task.k50h_robot_id is not None and simulation_state.grid:
                robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
                pick_task = await session.get(PickTask, event.pick_task_id)
                if pick_task is not None:
                    station = await session.get(Station, pick_task.station_id)
                    if station is not None:
                        await plan_and_store_path(
                            svc, robot.id,
                            (robot.grid_row, robot.grid_col),
                            (station.grid_row, station.grid_col),
                        )

                await svc.executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_CANTILEVER",
    })


@safe_handler
async def _handle_source_at_station(event) -> None:
    """SourceAtStation -> transition PickTask state."""
    logger.info("SourceAtStation: pick_task=%s station=%s", event.pick_task_id, event.station_id)

    async with handler_session() as session:
        from src.wes.application.pick_task_service import PickTaskService
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

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_STATION",
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

            if eq_task.a42td_robot_id is not None and eq_task.target_location_id is not None:
                robot = await svc.fm.get_robot(eq_task.a42td_robot_id)
                target_loc = await session.get(Location, eq_task.target_location_id)
                if target_loc is not None and simulation_state.grid:
                    await plan_and_store_path(
                        svc, robot.id,
                        (robot.grid_row, robot.grid_col),
                        (target_loc.grid_row, target_loc.grid_col),
                    )

                await svc.executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "RETURN_AT_CANTILEVER",
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
