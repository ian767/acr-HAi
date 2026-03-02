"""Physical cell-based FIFO station queue management."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Tuple, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.models import Station

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory reverse index: robot_id_str -> station_id_str
# Updated whenever _save_queue_state is called. Provides O(1) membership
# checks so that FleetManager.find_nearest_idle() can skip queue-bound
# robots without scanning every Station row and parsing JSON each time.
# ---------------------------------------------------------------------------
_queue_index: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Queue dissolve debounce: prevents all non-station slots from clearing
# simultaneously.  A full dissolve (old_occ >= 2 → 0) must persist for
# _DISSOLVE_DEBOUNCE_TICKS consecutive ticks before it is committed.
# ---------------------------------------------------------------------------
_dissolve_debounce: dict[str, int] = {}   # station_id_str -> first_empty_tick
_current_tick: int = 0
_DISSOLVE_DEBOUNCE_TICKS: int = 30


def set_current_tick(tick: int) -> None:
    """Called once per simulation tick to sync the module-level tick counter."""
    global _current_tick
    _current_tick = tick


def reset_dissolve_debounce() -> None:
    """Clear debounce state (called on simulation reset)."""
    _dissolve_debounce.clear()


def is_robot_in_any_queue(robot_id: uuid.UUID) -> bool:
    """O(1) check whether *robot_id* appears in any station queue slot."""
    return str(robot_id) in _queue_index


def rebuild_queue_index(stations) -> None:
    """Rebuild the index from station queue_state_json (sim start / reset)."""
    _queue_index.clear()
    for station in stations:
        if not station.queue_state_json:
            continue
        try:
            qs = json.loads(station.queue_state_json)
        except (json.JSONDecodeError, TypeError):
            continue
        sid = str(station.id)
        for key in ("station", "approach"):
            rid = qs.get(key)
            if rid:
                _queue_index[rid] = sid
        for slot in qs.get("queue", []):
            if slot:
                _queue_index[slot] = sid


def update_index_for_station(station_id: uuid.UUID, queue_state: dict) -> None:
    """Remove all entries mapped to *station_id*, then re-add from *queue_state*."""
    sid = str(station_id)
    stale = [rid for rid, s in _queue_index.items() if s == sid]
    for rid in stale:
        del _queue_index[rid]
    for key in ("station", "approach"):
        rid = queue_state.get(key)
        if rid:
            _queue_index[rid] = sid
    for slot in queue_state.get("queue", []):
        if slot:
            _queue_index[slot] = sid


def clear_queue_index() -> None:
    """Clear the in-memory index (called on simulation reset)."""
    _queue_index.clear()


class StationQueueService:
    """Manages the physical FIFO queue of robots waiting at stations.

    Each station has:
    - queue cells: ordered list of intermediate waiting positions (Q1, Q2, ...)
    - approach cell: final position before entering the station
    - station cell: the station itself (only one robot at a time)

    Flow: Qn -> ... -> Q1 -> approach -> station
    Robots are dispatched directly to the next available slot.

    INVARIANT: A robot may only appear in ONE station queue at a time.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Global invariant: one robot ↔ one station
    # ------------------------------------------------------------------

    async def clear_robot_from_all_queues(
        self, robot_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Remove *robot_id* from every station queue slot.

        Returns list of station IDs whose queue_state was modified.
        """
        result = await self._session.execute(select(Station))
        stations = result.scalars().all()
        rid = str(robot_id)
        changed_ids: list[uuid.UUID] = []

        for station in stations:
            if not station.queue_state_json:
                continue
            qs = self._get_queue_state(station)
            dirty = False
            for key in ("station", "approach"):
                if qs.get(key) == rid:
                    qs[key] = None
                    dirty = True
            for i, slot in enumerate(qs.get("queue", [])):
                if slot == rid:
                    qs["queue"][i] = None
                    dirty = True
            if dirty:
                self._save_queue_state(station, qs, reason="clear_robot")
                changed_ids.append(station.id)
                logger.info(
                    "Cleared robot %s from queue at station %s",
                    robot_id, station.name,
                )

        if changed_ids:
            await self._session.flush()
        return changed_ids

    async def find_next_slot(
        self, station_id: uuid.UUID,
    ) -> tuple[str, int | None, tuple[int, int] | None]:
        """Find the next available queue slot for a new robot.

        Assignment order (back-to-front):
        - If approach is empty → assign approach
        - Else if Q1 is empty → assign Q1
        - Else if Q2 is empty → assign Q2
        - ...
        - Else → full

        Returns ``(slot_name, slot_index, (row, col))`` where *slot_name*
        is ``"approach"``, ``"queue"`` (with *slot_index*), or ``"full"``.
        The cell coordinates are ``None`` when full.
        """
        station = await self._session.get(Station, station_id)
        if station is None:
            return ("full", None, None)

        queue_state = self._get_queue_state(station)
        queue_cells = self._get_queue_cells(station)

        # 1) Approach cell empty → assign approach
        if not queue_state.get("approach"):
            if station.approach_cell_row is not None:
                return ("approach", None, (station.approach_cell_row, station.approach_cell_col))
            # No dedicated approach cell — use station cell as approach
            return ("approach", None, (station.grid_row, station.grid_col))

        # 2) Approach occupied → find first empty queue slot (Q1, Q2, Q3...)
        q_slots = queue_state.get("queue", [])
        for i, slot in enumerate(q_slots):
            if not slot and i < len(queue_cells):
                cell = queue_cells[i]
                return ("queue", i, (cell["row"], cell["col"]))

        return ("full", None, None)

    async def place_in_slot(
        self, station_id: uuid.UUID, robot_id: uuid.UUID,
        slot_name: str, slot_index: int | None = None,
    ) -> dict:
        """Place a robot directly into a named queue slot.

        INVARIANT: first removes robot from ALL other queue slots globally
        so that it never appears in two stations simultaneously.
        """
        # ── Enforce global invariant ──
        await self.clear_robot_from_all_queues(robot_id)

        station = await self._session.get(Station, station_id)
        if station is None:
            raise ValueError(f"Station {station_id} not found")

        queue_state = self._get_queue_state(station)
        rid = str(robot_id)

        if slot_name == "approach":
            # Overwrite guard: log if a different robot already in approach
            _prev_ap = queue_state.get("approach")
            if _prev_ap and _prev_ap != rid:
                logger.warning(
                    "Approach overwrite: %s → %s at station %s",
                    _prev_ap[:8], rid[:8], station_id,
                )
            queue_state["approach"] = rid
            # TTL: robot must arrive at approach cell within 30 ticks
            queue_state["_approach_deadline_tick"] = _current_tick + 30
        elif slot_name == "queue" and slot_index is not None:
            q_slots = queue_state.get("queue", [])
            # Extend slots if queue_cells grew after initial state
            while slot_index >= len(q_slots):
                q_slots.append(None)
            q_slots[slot_index] = rid
        else:
            logger.warning("Cannot place robot %s in slot %s/%s", robot_id, slot_name, slot_index)
            return queue_state

        self._save_queue_state(station, queue_state, reason="place_in_slot")
        await self._session.flush()

        logger.info(
            "Robot %s placed in %s[%s] at station %s",
            robot_id, slot_name, slot_index, station_id,
        )
        return queue_state

    async def enter_queue(
        self, station_id: uuid.UUID, robot_id: uuid.UUID
    ) -> dict:
        """Robot enters the queue at the best available slot (legacy compat)."""
        # Enforce invariant first — clear from any previous queue.
        await self.clear_robot_from_all_queues(robot_id)

        station = await self._session.get(Station, station_id)
        if station is None:
            raise ValueError(f"Station {station_id} not found")

        slot_name, slot_index, _ = await self.find_next_slot(station_id)
        if slot_name == "full":
            logger.warning("Queue full at station %s — robot %s not placed", station_id, robot_id)
            return self._get_queue_state(station)

        return await self.place_in_slot(station_id, robot_id, slot_name, slot_index)

    async def advance_queue(self, station_id: uuid.UUID) -> dict | None:
        """Advance the FIFO queue: move robots forward where possible.

        Robots physically stay at the approach cell while being served
        (approach IS the serving position — robots never enter the station
        cell).  The "station" slot is purely informational.

        Returns the updated queue state or None if station not found.
        """
        station = await self._session.get(Station, station_id)
        if station is None:
            return None

        queue_state = self._get_queue_state(station)
        queue_cells = self._get_queue_cells(station)
        changed = False

        # Mark approach robot as being served (informational only —
        # robot physically stays at approach cell).
        if queue_state.get("approach") and not queue_state.get("station"):
            queue_state["station"] = queue_state["approach"]
            station.current_robot_id = uuid.UUID(queue_state["station"])
            # Clear approach — robot physically stays at approach cell but
            # the slot is freed so Q1 can promote and no ghost lingers.
            queue_state["approach"] = None
            changed = True

        # Compact the queue FIRST: shift all robots forward to fill gaps.
        # Multi-pass: [None, None, "Z", "K50H"] → ["Z", "K50H", None, None]
        # in a single advance_queue call.
        if queue_cells and queue_state.get("queue"):
            q_slots = queue_state["queue"]
            _shifted = True
            while _shifted:
                _shifted = False
                for i in range(len(q_slots) - 1):
                    if not q_slots[i] and q_slots[i + 1]:
                        q_slots[i] = q_slots[i + 1]
                        q_slots[i + 1] = None
                        _shifted = True
                        changed = True

            # Now try Q1 -> approach (only when approach is free)
            if q_slots and q_slots[0] and not queue_state.get("approach"):
                queue_state["approach"] = q_slots[0]
                queue_state["_approach_deadline_tick"] = _current_tick + 30
                q_slots[0] = None
                changed = True

                # Fill gap left by Q1 promotion
                for i in range(len(q_slots) - 1):
                    if not q_slots[i] and q_slots[i + 1]:
                        q_slots[i] = q_slots[i + 1]
                        q_slots[i + 1] = None

        if changed:
            self._save_queue_state(station, queue_state, reason="advance")
            await self._session.flush()

        return queue_state

    async def release_station(
        self, station_id: uuid.UUID, robot_id: uuid.UUID
    ) -> dict:
        """Release a robot from the station/approach position.

        Also clears any ghost entries in OTHER station queues (invariant).
        FIFO pull (_pull_advance_queues) handles advancing the next robot.
        """
        # Global cleanup — removes robot from ALL queues everywhere.
        await self.clear_robot_from_all_queues(robot_id)

        # Do NOT call advance_queue() — its compact logic shifts Q slot
        # assignments without physical movement, breaking FIFO pull.
        station = await self._session.get(Station, station_id)
        result = self._get_queue_state(station) if station else {}

        logger.info(
            "Robot %s released from station %s", robot_id, station_id,
        )
        return result

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

        # Robot in "station" or "approach" slot → stays at approach cell
        # (approach IS the serving position, robots never enter station cell).
        if queue_state.get("station") == rid or queue_state.get("approach") == rid:
            if station.approach_cell_row is not None:
                return (station.approach_cell_row, station.approach_cell_col)
            return (station.grid_row, station.grid_col)
        queue_cells = self._get_queue_cells(station)
        q_slots = queue_state.get("queue", [])
        for i, slot in enumerate(q_slots):
            if slot == rid and i < len(queue_cells):
                cell = queue_cells[i]
                return (cell["row"], cell["col"])

        return None

    def _get_queue_state(self, station: Station) -> dict:
        """Parse queue state from station's internal storage."""
        if station.queue_state_json:
            try:
                return json.loads(station.queue_state_json)
            except (json.JSONDecodeError, TypeError):
                pass

        # Default empty state
        queue_cells = self._get_queue_cells(station)
        num_slots = len(queue_cells)
        return {
            "station": str(station.current_robot_id) if station.current_robot_id else None,
            "approach": None,
            "queue": [None] * num_slots,
        }

    def _get_queue_cells(self, station: Station) -> list[dict]:
        """Parse queue cell definitions from JSON."""
        if not station.queue_cells_json:
            return []
        try:
            cells = json.loads(station.queue_cells_json)
            return sorted(cells, key=lambda c: c.get("position", 0))
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_queue_state(
        self, station: Station, state: dict,
        *, reason: str = "unknown", force_clear: bool = False,
    ) -> bool:
        """Persist queue state back to station model.

        Returns ``True`` on success.  Returns ``False`` when the dissolve
        debounce guard blocked the save (caller should NOT mark the station
        as dirty).
        """
        sid = str(station.id)

        # ── 1) Read old state for merge comparison ──
        old_state: dict = {}
        if station.queue_state_json:
            try:
                old_state = json.loads(station.queue_state_json)
            except (json.JSONDecodeError, TypeError):
                pass

        # ── 2) Merge guard: empty queue array ──
        new_queue = state.get("queue")
        old_queue = old_state.get("queue", [])
        if (new_queue is None or new_queue == []) and any(old_queue) and not force_clear:
            logger.warning(
                "Queue merge guard: preserving old queue at %s (reason=%s)",
                station.name, reason,
            )
            state["queue"] = old_queue

        # ── 3) All-empty debounce ──
        def _count_occ(qs: dict) -> int:
            n = 0
            if qs.get("approach"): n += 1
            for s in qs.get("queue", []):
                if s: n += 1
            return n

        old_occ = _count_occ(old_state)
        new_occ = _count_occ(state)

        if old_occ >= 2 and new_occ == 0 and not force_clear:
            first = _dissolve_debounce.get(sid)
            if first is None:
                _dissolve_debounce[sid] = _current_tick
                logger.warning(
                    "Queue dissolve BLOCKED at %s tick=%d (reason=%s, old_occ=%d)",
                    station.name, _current_tick, reason, old_occ,
                )
                return False
            elapsed = _current_tick - first
            if elapsed < _DISSOLVE_DEBOUNCE_TICKS:
                return False  # still debouncing
            logger.info(
                "Queue dissolve ALLOWED at %s after %d ticks",
                station.name, elapsed,
            )
            del _dissolve_debounce[sid]
        else:
            # Not an all-empty transition → reset debounce
            _dissolve_debounce.pop(sid, None)

        # ── 4) Invariant: station == approach same rid → clear approach ──
        _stn_rid = state.get("station")
        _ap_rid = state.get("approach")
        if _stn_rid and _ap_rid and _stn_rid == _ap_rid:
            state["approach"] = None
            state.pop("_approach_deadline_tick", None)
            logger.info(
                "INV station==approach: cleared approach duplicate %s at %s (reason=%s)",
                _stn_rid[:8], station.name, reason,
            )
        # Clear TTL when approach is empty
        if not state.get("approach"):
            state.pop("_approach_deadline_tick", None)

        # ── 5) Diagnostics ──
        state["_version_tick"] = _current_tick
        state["_mutation_reason"] = reason

        # ── 6) Save ──
        station.queue_state_json = json.dumps(state)
        if state.get("station"):
            station.current_robot_id = uuid.UUID(state["station"])
        else:
            station.current_robot_id = None
        # Keep the O(1) reverse index in sync.
        update_index_for_station(station.id, state)
        return True
