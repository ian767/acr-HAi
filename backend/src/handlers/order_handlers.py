"""Order-related event handlers."""

from __future__ import annotations

import logging

from src.handler_support import handler_session, safe_handler, ws_broadcast
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


def register(bus: EventBus) -> None:
    from src.wes.domain.events import (
        OrderAllocated,
        OrderCancelled,
        OrderCompleted,
        OrderCreated,
    )

    bus.subscribe(OrderCreated, _handle_order_created)
    bus.subscribe(OrderAllocated, _handle_order_allocated)
    bus.subscribe(OrderCompleted, _handle_order_completed)
    bus.subscribe(OrderCancelled, _handle_order_cancelled)


@safe_handler
async def _handle_order_created(event) -> None:
    logger.info("OrderCreated: %s", event.order_id)
    await ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "external_id": event.external_id,
        "sku": event.sku,
        "status": "NEW",
    })
    await ws_broadcast("order.status_changed", {
        "type": "order.status_changed",
        "orderId": str(event.order_id),
        "status": "NEW",
    })


@safe_handler
async def _handle_order_allocated(event) -> None:
    """OrderAllocated -> create PickTask (CREATED) -> reserve K50H -> SOURCE_REQUESTED."""
    logger.info("OrderAllocated: order=%s station=%s", event.order_id, event.station_id)

    async with handler_session() as session:
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

        pts = PickTaskService(session)
        # Creates PickTask in CREATED state
        pick_task = await pts.create_pick_task(
            order_id=order.id,
            station_id=event.station_id,
            sku=order.sku,
            qty=order.quantity,
        )

        # Find a tote for this SKU
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
            await session.flush()

            # Transition CREATED -> RESERVED -> SOURCE_REQUESTED
            # K50H reservation happens later when it picks up the tote
            # at the cantilever (via _handle_source_at_cantilever).
            await pts.transition_state(pick_task.id, "reserve")
            await pts.transition_state(pick_task.id, "request_source")

            for evt in pts.collect_events():
                await event_bus.publish(evt)

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

    await ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "ALLOCATED",
        "station_id": str(event.station_id),
    })
    await ws_broadcast("order.status_changed", {
        "type": "order.status_changed",
        "orderId": str(event.order_id),
        "status": "ALLOCATED",
    })


@safe_handler
async def _handle_order_completed(event) -> None:
    logger.info("OrderCompleted: %s", event.order_id)
    await ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "COMPLETED",
    })
    await ws_broadcast("order.status_changed", {
        "type": "order.status_changed",
        "orderId": str(event.order_id),
        "status": "COMPLETED",
    })


@safe_handler
async def _handle_order_cancelled(event) -> None:
    logger.info("OrderCancelled: %s", event.order_id)
    await ws_broadcast("order.updated", {
        "order_id": str(event.order_id),
        "status": "CANCELLED",
    })
    await ws_broadcast("order.status_changed", {
        "type": "order.status_changed",
        "orderId": str(event.order_id),
        "status": "CANCELLED",
    })
