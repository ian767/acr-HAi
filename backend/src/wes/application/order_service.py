"""Application service for Order lifecycle management."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.application.allocation_engine import AllocationEngine
from src.wes.application.inventory_service import InventoryService
from src.wes.domain.enums import OrderStatus
from src.wes.domain.events import (
    OrderAllocated,
    OrderCancelled,
    OrderCompleted,
    OrderCreated,
)
from src.wes.domain.models import Order
from src.wes.domain.state_machines import order_sm
from src.wes.infrastructure.repositories import OrderRepository

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrderRepository(session)
        self._allocation = AllocationEngine(session)
        self._inventory = InventoryService(session)
        self._events: list[object] = []

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_order(self, order_id: uuid.UUID) -> Order:
        order = await self._repo.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        return order

    async def list_orders(
        self,
        status: OrderStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Order]:
        rows = await self._repo.list(status=status, limit=limit, offset=offset)
        return list(rows)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def create_order(
        self,
        external_id: str,
        sku: str,
        quantity: int,
        priority: int = 0,
        zone_id: uuid.UUID | None = None,
        pbt_at: datetime | None = None,
    ) -> Order:
        order = Order(
            external_id=external_id,
            sku=sku,
            quantity=quantity,
            priority=priority,
            zone_id=zone_id,
            pbt_at=pbt_at,
            status=OrderStatus.NEW,
        )
        order = await self._repo.add(order)
        await self._session.commit()

        self._events.append(
            OrderCreated(
                order_id=order.id,
                external_id=external_id,
                sku=sku,
                quantity=quantity,
                priority=priority,
                zone_id=zone_id,
            )
        )
        logger.info("Order created: %s (external=%s)", order.id, external_id)
        return order

    async def allocate_order(self, order_id: uuid.UUID) -> Order:
        order = await self.get_order(order_id)

        # Transition: NEW -> ALLOCATING
        new_status, _ = order_sm.transition(order.status, "allocate")
        order.status = new_status
        await self._repo.update(order)

        # Run allocation engine
        station_id = await self._allocation.allocate(order)
        order.station_id = station_id

        # Transition: ALLOCATING -> ALLOCATED
        new_status, _ = order_sm.transition(order.status, "station_assigned")
        order.status = new_status
        await self._repo.update(order)
        await self._session.commit()

        self._events.append(
            OrderAllocated(order_id=order.id, station_id=station_id)
        )
        logger.info("Order allocated: %s -> station %s", order.id, station_id)
        return order

    async def cancel_order(self, order_id: uuid.UUID) -> Order:
        order = await self.get_order(order_id)

        new_status, side_effects = order_sm.transition(order.status, "cancel")
        order.status = new_status
        await self._repo.update(order)

        # Release inventory if it was previously allocated
        if "release_inventory" in side_effects and order.zone_id is not None:
            await self._inventory.release_stock(
                order.sku, order.zone_id, order.quantity
            )

        await self._session.commit()

        self._events.append(OrderCancelled(order_id=order.id))
        logger.info("Order cancelled: %s", order.id)
        return order

    async def complete_order(self, order_id: uuid.UUID) -> Order:
        order = await self.get_order(order_id)

        new_status, _ = order_sm.transition(order.status, "all_picked")
        order.status = new_status
        await self._repo.update(order)
        await self._session.commit()

        self._events.append(OrderCompleted(order_id=order.id))
        logger.info("Order completed: %s", order.id)
        return order

    # ------------------------------------------------------------------
    # Event access (for consumers / event bus integration)
    # ------------------------------------------------------------------

    def collect_events(self) -> list[object]:
        """Drain and return pending domain events."""
        events = list(self._events)
        self._events.clear()
        return events
