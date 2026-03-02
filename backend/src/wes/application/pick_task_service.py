"""Application service for PickTask lifecycle management."""

from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.enums import PickTaskState
from src.wes.domain.events import PickTaskCreated, PickTaskStateChanged
from src.wes.domain.models import PickTask
from src.wes.domain.state_machines import pick_task_sm
from src.wes.infrastructure.repositories import PickTaskRepository

logger = logging.getLogger(__name__)


class PickTaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PickTaskRepository(session)
        self._events: list[object] = []

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_pick_task(self, task_id: uuid.UUID) -> PickTask:
        task = await self._repo.get(task_id)
        if task is None:
            raise ValueError(f"PickTask {task_id} not found")
        return task

    async def list_pick_tasks(
        self,
        station_id: uuid.UUID | None = None,
        state: PickTaskState | None = None,
    ) -> list[PickTask]:
        rows = await self._repo.list(station_id=station_id, state=state)
        return list(rows)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def create_pick_task(
        self,
        order_id: uuid.UUID,
        station_id: uuid.UUID,
        sku: str,
        qty: int,
        source_tote_id: uuid.UUID | None = None,
    ) -> PickTask:
        task = PickTask(
            order_id=order_id,
            station_id=station_id,
            sku=sku,
            qty_to_pick=qty,
            qty_picked=0,
            source_tote_id=source_tote_id,
            state=PickTaskState.CREATED,
        )
        task = await self._repo.add(task)
        await self._session.commit()

        self._events.append(
            PickTaskCreated(
                pick_task_id=task.id,
                order_id=order_id,
                station_id=station_id,
                sku=sku,
                qty_to_pick=qty,
            )
        )
        logger.info("PickTask created: %s for order %s", task.id, order_id)
        return task

    async def complete_at_station(self, pick_task_id: uuid.UUID) -> PickTask:
        """Complete a pick task directly at the station (CV-1 route).

        Transitions: SOURCE_AT_STATION -> COMPLETED.
        """
        return await self.transition_state(pick_task_id, "complete")

    async def transition_state(
        self, pick_task_id: uuid.UUID, event: str
    ) -> PickTask:
        task = await self.get_pick_task(pick_task_id)
        previous_state = task.state

        new_state, side_effects = pick_task_sm.transition(task.state, event)
        task.state = new_state
        await self._repo.update(task)
        await self._session.commit()

        self._events.append(
            PickTaskStateChanged(
                pick_task_id=task.id,
                previous_state=previous_state.value,
                new_state=new_state.value,
                event=event,
            )
        )
        logger.info(
            "PickTask %s: %s -> %s (event=%s)",
            task.id,
            previous_state.value,
            new_state.value,
            event,
        )
        return task

    async def scan_item(self, pick_task_id: uuid.UUID) -> PickTask:
        """Increment qty_picked.  Auto-transitions to RETURN_REQUESTED
        when all items have been picked (triggers source tote return).

        Target-tote "Tote Full" is a separate operator action and does
        NOT block the source tote return flow.
        """
        task = await self.get_pick_task(pick_task_id)

        # If we are at SOURCE_AT_STATION we need to transition to PICKING first
        if task.state == PickTaskState.SOURCE_AT_STATION:
            task = await self.transition_state(pick_task_id, "scan_started")

        if task.state != PickTaskState.PICKING:
            raise ValueError(
                f"Cannot scan item: pick task {pick_task_id} is in state "
                f"{task.state.value!r}, expected PICKING"
            )

        task.qty_picked += 1
        await self._repo.update(task)

        # Auto-complete when all items picked → K50H returns source tote
        if task.qty_picked >= task.qty_to_pick:
            task = await self.transition_state(pick_task_id, "pick_complete")

        await self._session.commit()
        logger.info(
            "PickTask %s scanned item: %d/%d",
            task.id,
            task.qty_picked,
            task.qty_to_pick,
        )
        return task

    # ------------------------------------------------------------------
    # Event access
    # ------------------------------------------------------------------

    def collect_events(self) -> list[object]:
        """Drain and return pending domain events."""
        events = list(self._events)
        self._events.clear()
        return events
