"""Domain event handlers wiring WES <-> ESS <-> WebSocket."""

from __future__ import annotations

import logging
import uuid

from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


def register_handlers(bus: EventBus) -> None:
    """Subscribe all domain event handlers to the event bus."""
    from src.wes.domain.events import (
        OrderAllocated,
        OrderCancelled,
        OrderCompleted,
        OrderCreated,
        PickTaskStateChanged,
        RetrieveSourceTote,
        ReturnSourceTote,
    )
    from src.ess.domain.events import (
        ReturnAtCantilever,
        SourceAtCantilever,
        SourceAtStation,
        SourceBackInRack,
    )

    bus.subscribe(OrderCreated, _handle_order_created)
    bus.subscribe(OrderAllocated, _handle_order_allocated)
    bus.subscribe(OrderCompleted, _handle_order_completed)
    bus.subscribe(OrderCancelled, _handle_order_cancelled)
    bus.subscribe(RetrieveSourceTote, _handle_retrieve_source_tote)
    bus.subscribe(ReturnSourceTote, _handle_return_source_tote)
    bus.subscribe(PickTaskStateChanged, _handle_pick_task_state_changed)
    bus.subscribe(SourceAtCantilever, _handle_source_at_cantilever)
    bus.subscribe(SourceAtStation, _handle_source_at_station)
    bus.subscribe(ReturnAtCantilever, _handle_return_at_cantilever)
    bus.subscribe(SourceBackInRack, _handle_source_back_in_rack)

    logger.info("All event handlers registered")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ws_broadcast(message_type: str, payload: dict) -> None:
    from src.shared.websocket_manager import ws_manager
    await ws_manager.broadcast(message_type, payload)


def _get_session_factory():
    from src.shared.database import async_session_factory
    return async_session_factory


# ---------------------------------------------------------------------------
# Order event handlers
# ---------------------------------------------------------------------------


async def _handle_order_created(event) -> None:
    logger.info("OrderCreated: %s", event.order_id)
    await _ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "external_id": event.external_id,
        "sku": event.sku,
        "status": "NEW",
    })


async def _handle_order_allocated(event) -> None:
    """OrderAllocated -> create PickTask + dispatch RetrieveSourceTote."""
    logger.info("OrderAllocated: order=%s station=%s", event.order_id, event.station_id)

    factory = _get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.wes.domain.models import Order
        from src.wes.application.pick_task_service import PickTaskService
        from src.ess.domain.models import Tote
        from src.wes.domain.events import RetrieveSourceTote
        from src.shared.event_bus import event_bus

        order = await session.get(Order, event.order_id)
        if order is None:
            logger.error("Order %s not found for allocation", event.order_id)
            return

        # Create pick task
        pts = PickTaskService(session)
        pick_task = await pts.create_pick_task(
            order_id=order.id,
            station_id=event.station_id,
            sku=order.sku,
            qty=order.quantity,
        )

        # Find a tote with matching SKU
        result = await session.execute(
            select(Tote).where(
                Tote.sku == order.sku,
                Tote.quantity > 0,
                Tote.current_location_id.isnot(None),
            ).limit(1)
        )
        tote = result.scalar_one_or_none()

        if tote is not None:
            pick_task.source_tote_id = tote.id
            await session.commit()

            await event_bus.publish(RetrieveSourceTote(
                pick_task_id=pick_task.id,
                tote_id=tote.id,
                source_location_id=tote.current_location_id,
                station_id=event.station_id,
            ))
        else:
            logger.warning("No tote found for SKU %s", order.sku)
            await session.commit()

    await _ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "ALLOCATED",
        "station_id": str(event.station_id),
    })


async def _handle_order_completed(event) -> None:
    logger.info("OrderCompleted: %s", event.order_id)
    await _ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "COMPLETED",
    })


async def _handle_order_cancelled(event) -> None:
    logger.info("OrderCancelled: %s", event.order_id)
    await _ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "CANCELLED",
    })


# ---------------------------------------------------------------------------
# Equipment coordination handlers
# ---------------------------------------------------------------------------


async def _handle_retrieve_source_tote(event) -> None:
    """RetrieveSourceTote -> TaskExecutor.execute_retrieve() -> A* path -> Redis."""
    logger.info(
        "RetrieveSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    factory = _get_session_factory()
    async with factory() as session:
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis
        import src.shared.simulation_state as simulation_state

        fm = FleetManager(session)
        planner = PathPlanner(simulation_state.grid or [])
        traffic = simulation_state.traffic

        executor = TaskExecutor(session, fm, planner, traffic)
        eq_task = await executor.execute_retrieve(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            source_location_id=event.source_location_id,
            station_id=event.station_id,
        )

        # Compute A* path for A42TD and store in Redis
        if eq_task.a42td_robot_id is not None and simulation_state.grid:
            from src.ess.domain.models import Location

            robot = await fm.get_robot(eq_task.a42td_robot_id)
            source_loc = await session.get(Location, event.source_location_id)
            if source_loc is not None:
                path = planner.find_path(
                    (robot.grid_row, robot.grid_col),
                    (source_loc.grid_row, source_loc.grid_col),
                )
                if path:
                    redis_client = await get_redis()
                    cache = RobotStateCache(redis_client)
                    await cache.set_path(robot.id, path[1:])  # skip current position

            await executor.advance_task(eq_task.id, "a42td_dispatched")

        await session.commit()


async def _handle_return_source_tote(event) -> None:
    """ReturnSourceTote -> TaskExecutor.execute_return() -> A* path -> Redis."""
    logger.info(
        "ReturnSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    factory = _get_session_factory()
    async with factory() as session:
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis
        import src.shared.simulation_state as simulation_state

        fm = FleetManager(session)
        planner = PathPlanner(simulation_state.grid or [])
        traffic = simulation_state.traffic

        executor = TaskExecutor(session, fm, planner, traffic)
        eq_task = await executor.execute_return(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            target_location_id=event.target_location_id,
            station_id=event.station_id,
        )

        # Compute A* path for K50H (station -> cantilever) and store in Redis
        if eq_task.k50h_robot_id is not None and simulation_state.grid:
            from src.ess.domain.models import Location
            from src.wes.domain.models import Station

            robot = await fm.get_robot(eq_task.k50h_robot_id)
            station = await session.get(Station, event.station_id)
            if station is not None:
                # Find nearest cantilever to route through
                target_loc = await session.get(Location, event.target_location_id)
                if target_loc is not None:
                    path = planner.find_path(
                        (robot.grid_row, robot.grid_col),
                        (target_loc.grid_row, target_loc.grid_col),
                    )
                    if path:
                        redis_client = await get_redis()
                        cache = RobotStateCache(redis_client)
                        await cache.set_path(robot.id, path[1:])

            await executor.advance_task(eq_task.id, "a42td_dispatched")

        await session.commit()


# ---------------------------------------------------------------------------
# PickTask state change handler
# ---------------------------------------------------------------------------


async def _handle_pick_task_state_changed(event) -> None:
    """PickTaskStateChanged -> dispatch ReturnSourceTote on RETURN_REQUESTED."""
    logger.info(
        "PickTaskStateChanged: %s %s -> %s",
        event.pick_task_id, event.previous_state, event.new_state,
    )

    if event.new_state == "RETURN_REQUESTED":
        factory = _get_session_factory()
        async with factory() as session:
            from src.wes.domain.models import PickTask
            from src.ess.domain.models import Tote
            from src.wes.domain.events import ReturnSourceTote
            from src.shared.event_bus import event_bus

            pick_task = await session.get(PickTask, event.pick_task_id)
            if pick_task is None:
                return

            if pick_task.source_tote_id is not None:
                tote = await session.get(Tote, pick_task.source_tote_id)
                if tote is not None and tote.home_location_id is not None:
                    await event_bus.publish(ReturnSourceTote(
                        pick_task_id=pick_task.id,
                        tote_id=tote.id,
                        target_location_id=tote.home_location_id,
                        station_id=pick_task.station_id,
                    ))

    await _ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "previous_state": event.previous_state,
        "new_state": event.new_state,
    })


# ---------------------------------------------------------------------------
# ESS arrival event handlers
# ---------------------------------------------------------------------------


async def _handle_source_at_cantilever(event) -> None:
    """SourceAtCantilever -> transition PickTask + dispatch K50H."""
    logger.info("SourceAtCantilever: pick_task=%s", event.pick_task_id)

    factory = _get_session_factory()
    async with factory() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis
        from src.wes.domain.models import Station
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        # Transition PickTask state
        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_at_cantilever")

        # Publish PickTask events
        from src.shared.event_bus import event_bus
        for evt in pts.collect_events():
            await event_bus.publish(evt)

        # Find the RETRIEVE EquipmentTask and advance it
        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETRIEVE,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()

        if eq_task is not None:
            fm = FleetManager(session)
            planner = PathPlanner(simulation_state.grid or [])
            traffic = simulation_state.traffic
            executor = TaskExecutor(session, fm, planner, traffic)
            await executor.advance_task(eq_task.id, "at_cantilever")

            # Plan K50H path: cantilever -> station
            if eq_task.k50h_robot_id is not None and simulation_state.grid:
                from src.wes.domain.models import PickTask

                robot = await fm.get_robot(eq_task.k50h_robot_id)
                pick_task = await session.get(PickTask, event.pick_task_id)
                if pick_task is not None:
                    station = await session.get(Station, pick_task.station_id)
                    if station is not None:
                        path = planner.find_path(
                            (robot.grid_row, robot.grid_col),
                            (station.grid_row, station.grid_col),
                        )
                        if path:
                            redis_client = await get_redis()
                            cache = RobotStateCache(redis_client)
                            await cache.set_path(robot.id, path[1:])

                await executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

    await _ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_CANTILEVER",
    })


async def _handle_source_at_station(event) -> None:
    """SourceAtStation -> transition PickTask state."""
    logger.info("SourceAtStation: pick_task=%s station=%s", event.pick_task_id, event.station_id)

    factory = _get_session_factory()
    async with factory() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_at_station")

        from src.shared.event_bus import event_bus
        for evt in pts.collect_events():
            await event_bus.publish(evt)

        # Advance equipment task to DELIVERED
        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETRIEVE,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            fm = FleetManager(session)
            planner = PathPlanner(simulation_state.grid or [])
            traffic = simulation_state.traffic
            executor = TaskExecutor(session, fm, planner, traffic)
            await executor.advance_task(eq_task.id, "delivered")

        await session.commit()

    await _ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "SOURCE_AT_STATION",
    })


async def _handle_return_at_cantilever(event) -> None:
    """ReturnAtCantilever -> transition PickTask + dispatch A42TD for return leg."""
    logger.info("ReturnAtCantilever: pick_task=%s", event.pick_task_id)

    factory = _get_session_factory()
    async with factory() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.ess.domain.models import Location
        from src.shared.redis import get_redis
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "return_at_cantilever")

        from src.shared.event_bus import event_bus
        for evt in pts.collect_events():
            await event_bus.publish(evt)

        # Find RETURN EquipmentTask and advance it + plan A42TD path
        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            fm = FleetManager(session)
            planner = PathPlanner(simulation_state.grid or [])
            traffic = simulation_state.traffic
            executor = TaskExecutor(session, fm, planner, traffic)
            await executor.advance_task(eq_task.id, "at_cantilever")

            # Plan A42TD path: cantilever -> rack
            if eq_task.a42td_robot_id is not None and eq_task.target_location_id is not None:
                robot = await fm.get_robot(eq_task.a42td_robot_id)
                target_loc = await session.get(Location, eq_task.target_location_id)
                if target_loc is not None and simulation_state.grid:
                    path = planner.find_path(
                        (robot.grid_row, robot.grid_col),
                        (target_loc.grid_row, target_loc.grid_col),
                    )
                    if path:
                        redis_client = await get_redis()
                        cache = RobotStateCache(redis_client)
                        await cache.set_path(robot.id, path[1:])

                await executor.advance_task(eq_task.id, "k50h_dispatched")

        await session.commit()

    await _ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "RETURN_AT_CANTILEVER",
    })


async def _handle_source_back_in_rack(event) -> None:
    """SourceBackInRack -> complete PickTask + check Order completion."""
    logger.info("SourceBackInRack: pick_task=%s", event.pick_task_id)

    factory = _get_session_factory()
    async with factory() as session:
        from src.wes.application.pick_task_service import PickTaskService
        from src.wes.domain.models import PickTask, Order
        from src.wes.domain.enums import PickTaskState
        from src.wes.application.order_service import OrderService
        from src.ess.domain.models import EquipmentTask
        from src.ess.domain.enums import EquipmentTaskType
        from src.ess.application.fleet_manager import FleetManager
        from src.ess.application.path_planner import PathPlanner
        from src.ess.application.task_executor import TaskExecutor
        import src.shared.simulation_state as simulation_state
        from sqlalchemy import select

        pts = PickTaskService(session)
        await pts.transition_state(event.pick_task_id, "source_back_in_rack")

        from src.shared.event_bus import event_bus
        for evt in pts.collect_events():
            await event_bus.publish(evt)

        # Complete equipment task
        result = await session.execute(
            select(EquipmentTask).where(
                EquipmentTask.pick_task_id == event.pick_task_id,
                EquipmentTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        eq_task = result.scalar_one_or_none()
        if eq_task is not None:
            fm = FleetManager(session)
            planner = PathPlanner(simulation_state.grid or [])
            traffic = simulation_state.traffic
            executor = TaskExecutor(session, fm, planner, traffic)
            try:
                await executor.advance_task(eq_task.id, "delivered")
                await executor.advance_task(eq_task.id, "completed")
            except ValueError:
                pass  # May already be in correct state

        # Check if all pick tasks for the order are completed
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

    await _ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "new_state": "COMPLETED",
    })
