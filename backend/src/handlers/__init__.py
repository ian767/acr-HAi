"""Handlers package: domain event handler registration."""

from __future__ import annotations

import logging

from src.shared.event_bus import EventBus

from src.handlers import (
    arrival_handlers,
    equipment_handlers,
    order_handlers,
    pick_task_handlers,
)

logger = logging.getLogger(__name__)


def register_all_handlers(bus: EventBus) -> None:
    """Register all domain event handlers on the given event bus."""
    order_handlers.register(bus)
    equipment_handlers.register(bus)
    pick_task_handlers.register(bus)
    arrival_handlers.register(bus)
    logger.info("All event handlers registered")
