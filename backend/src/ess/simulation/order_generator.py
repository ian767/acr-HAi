"""Automatic order generation for WES-driven simulation."""

from __future__ import annotations

import logging
import random
import uuid

logger = logging.getLogger(__name__)


class OrderGenerator:
    """PhysicsEngine updatable that periodically creates orders.

    Each tick accumulates time; once enough time has elapsed to generate
    an order (based on ``orders_per_minute``), it creates an order via
    :class:`OrderService`, allocates it, and publishes the resulting
    domain events through the event bus.

    Caps active (non-completed) orders to avoid overwhelming the robot fleet.
    """

    def __init__(
        self,
        orders_per_minute: float = 6.0,
        zone_id: uuid.UUID | None = None,
        max_active_orders: int = 4,
    ) -> None:
        self._orders_per_minute = max(0.1, orders_per_minute)
        self._zone_id = zone_id
        self._max_active = max_active_orders
        self._accumulated_time: float = 0.0
        self._order_counter: int = 0
        self._available_skus: list[str] | None = None

    async def update(self, dt: float) -> None:
        """Called once per simulation tick by PhysicsEngine."""
        if self._orders_per_minute <= 0:
            return

        interval = 60.0 / self._orders_per_minute
        self._accumulated_time += dt

        if self._accumulated_time < interval:
            return

        self._accumulated_time -= interval

        # Cap active orders to avoid overwhelming robots.
        if await self._active_order_count() >= self._max_active:
            return

        await self._generate_order()

    async def _active_order_count(self) -> int:
        """Count orders that are not yet COMPLETED or CANCELLED."""
        try:
            from src.handler_support import handler_session
            from src.wes.domain.models import Order
            from src.wes.domain.enums import OrderStatus
            from sqlalchemy import select, func

            async with handler_session() as session:
                result = await session.execute(
                    select(func.count(Order.id)).where(
                        Order.status.notin_([
                            OrderStatus.COMPLETED,
                            OrderStatus.CANCELLED,
                        ])
                    )
                )
                return result.scalar() or 0
        except Exception:
            return 0

    async def _generate_order(self) -> None:
        """Create and allocate a single order."""
        from src.handler_support import handler_session
        from src.wes.application.order_service import OrderService
        from src.shared.event_bus import event_bus

        # Load available SKUs once.
        if self._available_skus is None:
            await self._load_skus()
        if not self._available_skus:
            return

        self._order_counter += 1
        sku = random.choice(self._available_skus)
        qty = random.randint(1, 3)
        external_id = f"SIM-{self._order_counter:06d}"

        try:
            async with handler_session() as session:
                svc = OrderService(session)
                order = await svc.create_order(
                    external_id=external_id,
                    sku=sku,
                    quantity=qty,
                    priority=random.randint(0, 5),
                    zone_id=self._zone_id,
                )

                # Publish OrderCreated events.
                for evt in svc.collect_events():
                    await event_bus.publish(evt)

                # Allocate the order (triggers the full WES chain).
                await svc.allocate_order(order.id)
                for evt in svc.collect_events():
                    await event_bus.publish(evt)

            logger.info(
                "Generated order %s: SKU=%s qty=%d",
                external_id, sku, qty,
            )
        except Exception:
            logger.exception("Failed to generate order %s", external_id)

    async def _load_skus(self) -> None:
        """Load available SKUs from inventory."""
        try:
            from src.handler_support import handler_session
            from src.wes.domain.models import Inventory
            from sqlalchemy import select

            async with handler_session() as session:
                result = await session.execute(
                    select(Inventory.sku).where(Inventory.total_qty > 0)
                )
                self._available_skus = [row[0] for row in result.all()]

            if not self._available_skus:
                logger.warning("No SKUs with inventory found for order generation")
        except Exception:
            logger.exception("Failed to load SKUs")
            self._available_skus = []
