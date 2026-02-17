"""PickTask state change event handler."""

from __future__ import annotations

import logging

from src.handler_support import handler_session, safe_handler, ws_broadcast
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


def register(bus: EventBus) -> None:
    from src.wes.domain.events import PickTaskStateChanged

    bus.subscribe(PickTaskStateChanged, _handle_pick_task_state_changed)


@safe_handler
async def _handle_pick_task_state_changed(event) -> None:
    """PickTaskStateChanged -> dispatch ReturnSourceTote on RETURN_REQUESTED."""
    logger.info(
        "PickTaskStateChanged: %s %s -> %s",
        event.pick_task_id, event.previous_state, event.new_state,
    )

    if event.new_state == "RETURN_REQUESTED":
        async with handler_session() as session:
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

    await ws_broadcast("pick_task.updated", {
        "pick_task_id": str(event.pick_task_id),
        "previous_state": event.previous_state,
        "new_state": event.new_state,
    })
