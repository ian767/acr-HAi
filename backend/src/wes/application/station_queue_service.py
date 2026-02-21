"""Physical cell-based FIFO station queue management."""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.models import Station

logger = logging.getLogger(__name__)


class StationQueueService:
    """Manages the physical FIFO queue of robots waiting at stations.

    Each station has:
    - holding cell: entry point where robots first arrive
    - queue cells: ordered list of intermediate waiting positions (Q1, Q2, ...)
    - approach cell: final position before entering the station
    - station cell: the station itself (only one robot at a time)

    Flow: holding -> Qn -> ... -> Q1 -> approach -> station
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enter_queue(
        self, station_id: uuid.UUID, robot_id: uuid.UUID
    ) -> dict:
        """Robot enters the queue at the holding cell position."""
        station = await self._session.get(Station, station_id)
        if station is None:
            raise ValueError(f"Station {station_id} not found")

        queue_state = self._get_queue_state(station)
        # Place robot in holding cell
        queue_state["holding"] = str(robot_id)
        self._save_queue_state(station, queue_state)
        await self._session.flush()

        logger.info(
            "Robot %s entered queue at station %s (holding cell)",
            robot_id, station_id,
        )
        return queue_state

    async def advance_queue(self, station_id: uuid.UUID) -> dict | None:
        """Advance the FIFO queue: move robots forward where possible.

        Returns the updated queue state or None if station not found.
        """
        station = await self._session.get(Station, station_id)
        if station is None:
            return None

        queue_state = self._get_queue_state(station)
        queue_cells = self._get_queue_cells(station)
        changed = False

        # Try to move from approach -> station
        if queue_state.get("approach") and not queue_state.get("station"):
            queue_state["station"] = queue_state["approach"]
            queue_state["approach"] = None
            station.current_robot_id = uuid.UUID(queue_state["station"])
            changed = True

        # Try to move from Q1 -> approach (Q1 is index 0 in queue_cells)
        if queue_cells and queue_state.get("queue"):
            q_slots = queue_state["queue"]
            if q_slots and q_slots[0] and not queue_state.get("approach"):
                queue_state["approach"] = q_slots[0]
                q_slots[0] = None
                changed = True

            # Shift queue forward: Q2->Q1, Q3->Q2, etc.
            for i in range(len(q_slots) - 1):
                if not q_slots[i] and q_slots[i + 1]:
                    q_slots[i] = q_slots[i + 1]
                    q_slots[i + 1] = None
                    changed = True

        # Try to move from holding -> last Q slot (or approach if no Q)
        if queue_state.get("holding"):
            if queue_cells and queue_state.get("queue"):
                # Move to last Q slot if empty
                q_slots = queue_state["queue"]
                last_idx = len(q_slots) - 1
                if last_idx >= 0 and not q_slots[last_idx]:
                    q_slots[last_idx] = queue_state["holding"]
                    queue_state["holding"] = None
                    changed = True
            elif not queue_state.get("approach"):
                # No queue cells, go directly to approach
                queue_state["approach"] = queue_state["holding"]
                queue_state["holding"] = None
                changed = True

        if changed:
            self._save_queue_state(station, queue_state)
            await self._session.flush()

        return queue_state

    async def release_station(
        self, station_id: uuid.UUID, robot_id: uuid.UUID
    ) -> dict:
        """Release a robot from the station position and advance queue."""
        station = await self._session.get(Station, station_id)
        if station is None:
            raise ValueError(f"Station {station_id} not found")

        queue_state = self._get_queue_state(station)

        if queue_state.get("station") == str(robot_id):
            queue_state["station"] = None
            station.current_robot_id = None

        self._save_queue_state(station, queue_state)
        await self._session.flush()

        # Trigger advance to fill the vacated station slot
        result = await self.advance_queue(station_id)

        logger.info(
            "Robot %s released from station %s", robot_id, station_id,
        )
        return result or queue_state

    async def get_queue_state(self, station_id: uuid.UUID) -> dict | None:
        """Return the current queue occupancy state."""
        station = await self._session.get(Station, station_id)
        if station is None:
            return None
        return self._get_queue_state(station)

    async def get_robot_target_cell(
        self, station_id: uuid.UUID, robot_id: uuid.UUID
    ) -> tuple[int, int] | None:
        """Return the cell coordinates where this robot should move to in the queue."""
        station = await self._session.get(Station, station_id)
        if station is None:
            return None

        queue_state = self._get_queue_state(station)
        rid = str(robot_id)

        if queue_state.get("station") == rid:
            return (station.grid_row, station.grid_col)
        if queue_state.get("approach") == rid:
            if station.approach_cell_row is not None:
                return (station.approach_cell_row, station.approach_cell_col)
        if queue_state.get("holding") == rid:
            if station.holding_cell_row is not None:
                return (station.holding_cell_row, station.holding_cell_col)

        queue_cells = self._get_queue_cells(station)
        q_slots = queue_state.get("queue", [])
        for i, slot in enumerate(q_slots):
            if slot == rid and i < len(queue_cells):
                cell = queue_cells[i]
                return (cell["row"], cell["col"])

        return None

    def _get_queue_state(self, station: Station) -> dict:
        """Parse queue state from station's internal storage."""
        queue_cells = self._get_queue_cells(station)
        num_slots = len(queue_cells)

        # Default empty state
        default = {
            "station": str(station.current_robot_id) if station.current_robot_id else None,
            "approach": None,
            "queue": [None] * num_slots,
            "holding": None,
        }
        return default

    def _get_queue_cells(self, station: Station) -> list[dict]:
        """Parse queue cell definitions from JSON."""
        if not station.queue_cells_json:
            return []
        try:
            cells = json.loads(station.queue_cells_json)
            return sorted(cells, key=lambda c: c.get("position", 0))
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_queue_state(self, station: Station, state: dict) -> None:
        """Persist queue state back to station model."""
        if state.get("station"):
            station.current_robot_id = uuid.UUID(state["station"])
        else:
            station.current_robot_id = None
