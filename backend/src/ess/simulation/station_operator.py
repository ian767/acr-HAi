"""Automatic station operator for WES-driven simulation.

Simulates a human operator at each station: when a tote arrives
(PickTask in SOURCE_AT_STATION state) AND a robot is physically
present at the station, waits a configurable number of ticks then
calls scan_item() to complete the pick, triggering the return flow.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class StationOperator:
    """PhysicsEngine updatable that auto-processes picks at stations.

    Tracks per-task wait counters. Once a task has been in
    SOURCE_AT_STATION for ``processing_ticks`` ticks AND a robot
    holding the tote is confirmed at the station, it calls
    ``PickTaskService.scan_item()`` which completes the pick and
    triggers the return flow via the event bus.
    """

    def __init__(self, processing_ticks: int = 5) -> None:
        self._processing_ticks = max(1, processing_ticks)
        # task_id -> ticks waited
        self._wait_counters: dict[uuid.UUID, int] = {}

    async def update(self, dt: float) -> None:
        """Called once per simulation tick by PhysicsEngine."""
        from src.handler_support import handler_session
        from src.wes.application.pick_task_service import PickTaskService
        from src.wes.domain.enums import PickTaskState
        from src.wes.domain.models import PickTask, Station
        from src.ess.domain.models import Robot
        from src.shared.event_bus import event_bus
        from sqlalchemy import select

        # IMPORTANT: Collect events inside the session, then publish them
        # AFTER the session is closed.  Event handlers open their own
        # write sessions, and SQLite doesn't support concurrent
        # transactions — publishing inside would cause "database locked".
        pending_events: list = []

        try:
            async with handler_session() as session:
                # Find all pick tasks at station (SOURCE_AT_STATION or PICKING).
                result = await session.execute(
                    select(PickTask).where(
                        PickTask.state.in_([
                            PickTaskState.SOURCE_AT_STATION,
                            PickTaskState.PICKING,
                        ])
                    )
                )
                tasks = result.scalars().all()

                if not tasks:
                    self._wait_counters.clear()
                    return

                # Clean up counters for tasks no longer at station.
                active_ids = {t.id for t in tasks}
                stale = [tid for tid in self._wait_counters if tid not in active_ids]
                for tid in stale:
                    del self._wait_counters[tid]

                for task in tasks:
                    # --- CV-1: Verify robot is physically at station ---
                    if task.state == PickTaskState.SOURCE_AT_STATION:
                        robot_present = await self._verify_robot_at_station(
                            session, task.id, task.station_id
                        )
                        if not robot_present:
                            # No robot at station - don't process this task
                            continue

                    count = self._wait_counters.get(task.id, 0) + 1
                    self._wait_counters[task.id] = count

                    if count >= self._processing_ticks:
                        # Time to scan.
                        pts = PickTaskService(session)
                        try:
                            updated_task = await pts.scan_item(task.id)
                            pending_events.extend(pts.collect_events())
                            logger.info(
                                "Station operator scanned PickTask %s (%d/%d)",
                                task.id, updated_task.qty_picked, updated_task.qty_to_pick,
                            )

                            # Return flow is triggered automatically:
                            # scan_item() → RETURN_REQUESTED state change →
                            # PickTaskStateChanged event → pick_task_handlers
                            # publishes ReturnSourceTote.  No explicit publish
                            # needed here (doing so would create duplicates).
                        except Exception:
                            logger.exception(
                                "Station operator failed to scan PickTask %s",
                                task.id,
                            )

                        # Reset counter (will pick again next cycle if qty remains).
                        self._wait_counters[task.id] = 0

        except Exception:
            logger.exception("StationOperator update failed")

        # Publish events OUTSIDE the session to avoid SQLite lock conflicts.
        for evt in pending_events:
            await event_bus.publish(evt)

    @staticmethod
    async def _verify_robot_at_station(
        session, pick_task_id: uuid.UUID, station_id: uuid.UUID
    ) -> bool:
        """Check if a robot holding this pick task's tote is at the station."""
        from src.ess.domain.models import Robot
        from src.wes.domain.models import Station
        from sqlalchemy import select

        # Method 1: Check station.current_robot_id
        station = await session.get(Station, station_id)
        if station is not None and station.current_robot_id is not None:
            robot = await session.get(Robot, station.current_robot_id)
            if (robot is not None
                    and robot.hold_pick_task_id == pick_task_id
                    and robot.hold_at_station):
                return True

        # Method 2: Search for any robot reserved for this station with hold_at_station
        result = await session.execute(
            select(Robot).where(
                Robot.hold_at_station == True,  # noqa: E712
                Robot.hold_pick_task_id == pick_task_id,
                Robot.reservation_station_id == station_id,
            ).limit(1)
        )
        robot = result.scalar_one_or_none()
        return robot is not None
