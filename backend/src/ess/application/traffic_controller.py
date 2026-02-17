"""Traffic control: cell reservation, congestion tracking, and deadlock detection."""

from __future__ import annotations

import uuid
from collections import defaultdict


class TrafficController:
    """In-memory cell-level traffic controller for a single zone.

    Each cell ``(row, col)`` may be reserved by at most one robot at a time.
    """

    def __init__(self) -> None:
        # Maps (row, col) -> robot_id that currently holds the reservation.
        self._occupied: dict[tuple[int, int], uuid.UUID] = {}
        # Tracks how many times each cell has been reserved (for congestion).
        self._reservation_counts: dict[tuple[int, int], int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Reservation
    # ------------------------------------------------------------------

    def reserve_cell(self, row: int, col: int, robot_id: uuid.UUID) -> bool:
        """Try to reserve a cell for *robot_id*.

        Returns ``True`` on success.  Returns ``False`` if the cell is
        already occupied by a *different* robot.  Re-reserving by the
        same robot is idempotent and succeeds.
        """
        key = (row, col)
        occupant = self._occupied.get(key)
        if occupant is not None and occupant != robot_id:
            return False
        self._occupied[key] = robot_id
        self._reservation_counts[key] += 1
        return True

    def release_cell(self, row: int, col: int, robot_id: uuid.UUID) -> None:
        """Release a previously reserved cell.

        Only the owning robot may release; other callers are silently ignored.
        """
        key = (row, col)
        if self._occupied.get(key) == robot_id:
            del self._occupied[key]

    # ------------------------------------------------------------------
    # Congestion
    # ------------------------------------------------------------------

    def get_congestion_map(self) -> dict[tuple[int, int], float]:
        """Return a mapping of ``(row, col)`` to a congestion cost.

        The cost is derived from the cumulative reservation count for
        each cell, normalised so that a cell with the maximum reservation
        count maps to ``1.0``.
        """
        if not self._reservation_counts:
            return {}
        max_count = max(self._reservation_counts.values())
        if max_count == 0:
            return {}
        return {
            cell: count / max_count
            for cell, count in self._reservation_counts.items()
        }

    # ------------------------------------------------------------------
    # Deadlock detection
    # ------------------------------------------------------------------

    def detect_deadlock(self, robots: list) -> list[uuid.UUID]:
        """Simple cycle detection among robots that are mutually blocking.

        Each robot is expected to expose ``id`` (UUID), ``grid_row``,
        ``grid_col``, and a ``_next_cell`` attribute (the ``(row, col)``
        the robot wants to move into).  Robots without ``_next_cell`` are
        not considered.

        Returns a list of robot IDs that form a deadlock cycle.
        """
        # Build a wait-for graph: robot_id -> robot_id it's blocked by.
        wait_for: dict[uuid.UUID, uuid.UUID] = {}
        for robot in robots:
            next_cell = getattr(robot, "_next_cell", None)
            if next_cell is None:
                continue
            blocker_id = self._occupied.get(next_cell)
            if blocker_id is not None and blocker_id != robot.id:
                wait_for[robot.id] = blocker_id

        # Walk chains to find cycles.
        visited: set[uuid.UUID] = set()
        deadlocked: list[uuid.UUID] = []

        for start in wait_for:
            if start in visited:
                continue
            path: list[uuid.UUID] = []
            path_set: set[uuid.UUID] = set()
            current: uuid.UUID | None = start

            while current is not None and current not in visited:
                if current in path_set:
                    # Found a cycle -- extract it.
                    cycle_start = path.index(current)
                    cycle = path[cycle_start:]
                    deadlocked.extend(cycle)
                    visited.update(cycle)
                    break
                path.append(current)
                path_set.add(current)
                current = wait_for.get(current)
            visited.update(path)

        return deadlocked

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def occupied_cells(self) -> dict[tuple[int, int], uuid.UUID]:
        """Read-only snapshot of current reservations."""
        return dict(self._occupied)
