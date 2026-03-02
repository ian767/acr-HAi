"""Traffic control: cell reservation, congestion tracking, and deadlock detection."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict

logger = logging.getLogger(__name__)


class TrafficController:
    """In-memory cell-level traffic controller for a single zone.

    Two separate maps track cell ownership:

    - ``_position``: cells where a robot physically IS right now.
      These are set at initialisation and after each successful move.
      They must NEVER be force-released (the robot is there).
    - ``_forward``: cells a robot has reserved for its next move but
      has not yet physically entered.  These CAN become stale and are
      subject to TTL-based force-release.

    Both maps block other robots from entering.
    """

    def __init__(self) -> None:
        # Current physical position: (row, col) -> robot_id.
        self._position: dict[tuple[int, int], uuid.UUID] = {}
        # Forward reservation (next-cell): (row, col) -> robot_id.
        self._forward: dict[tuple[int, int], uuid.UUID] = {}
        # Tracks how many times each cell has been reserved (for congestion).
        self._reservation_counts: dict[tuple[int, int], int] = defaultdict(int)
        # Maps (row, col) -> tick when forward reservation was made (age tracking).
        self._forward_tick: dict[tuple[int, int], int] = {}
        self._current_tick: int = 0

    def set_tick(self, tick: int) -> None:
        """Update current tick for reservation age tracking."""
        self._current_tick = tick

    # ------------------------------------------------------------------
    # Position management (current physical location)
    # ------------------------------------------------------------------

    def set_position(self, row: int, col: int, robot_id: uuid.UUID) -> None:
        """Register a robot's current physical position.

        Called at initialisation and after confirm_move().
        Idempotent — re-setting the same cell is a no-op.
        """
        self._position[(row, col)] = robot_id
        self._reservation_counts[(row, col)] += 1

    # ------------------------------------------------------------------
    # Forward reservation (next-cell)
    # ------------------------------------------------------------------

    def reserve_cell(self, row: int, col: int, robot_id: uuid.UUID) -> bool:
        """Try to reserve a cell for *robot_id*'s next move.

        Returns ``True`` on success.  Returns ``False`` if the cell is
        already occupied (position or forward) by a *different* robot.
        Re-reserving by the same robot is idempotent and succeeds.
        """
        key = (row, col)
        # Check position map
        pos_occupant = self._position.get(key)
        if pos_occupant is not None and pos_occupant != robot_id:
            return False
        # Check forward map
        fwd_occupant = self._forward.get(key)
        if fwd_occupant is not None and fwd_occupant != robot_id:
            return False
        # If robot already holds this as position, no forward needed.
        if pos_occupant == robot_id:
            return True
        self._forward[key] = robot_id
        self._reservation_counts[key] += 1
        if key not in self._forward_tick:
            self._forward_tick[key] = self._current_tick
        return True

    def confirm_move(
        self, old_row: int, old_col: int,
        new_row: int, new_col: int, robot_id: uuid.UUID,
    ) -> None:
        """Commit a robot's move: release old position, promote forward → position."""
        old_key = (old_row, old_col)
        new_key = (new_row, new_col)
        # Release old position
        if self._position.get(old_key) == robot_id:
            del self._position[old_key]
        # Promote forward → position
        if self._forward.get(new_key) == robot_id:
            del self._forward[new_key]
            self._forward_tick.pop(new_key, None)
        self._position[new_key] = robot_id

    def release_cell(self, row: int, col: int, robot_id: uuid.UUID) -> None:
        """Release a cell from either map.  Only the owning robot may release."""
        key = (row, col)
        if self._position.get(key) == robot_id:
            del self._position[key]
        if self._forward.get(key) == robot_id:
            del self._forward[key]
            self._forward_tick.pop(key, None)

    # ------------------------------------------------------------------
    # Congestion
    # ------------------------------------------------------------------

    def get_congestion_map(self) -> dict[tuple[int, int], float]:
        """Return a mapping of ``(row, col)`` to a congestion cost."""
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
        """Simple cycle detection among robots that are mutually blocking."""
        wait_for: dict[uuid.UUID, uuid.UUID] = {}
        all_occupied = self.occupied_cells
        for robot in robots:
            next_cell = getattr(robot, "_next_cell", None)
            if next_cell is None:
                continue
            blocker_id = all_occupied.get(next_cell)
            if blocker_id is not None and blocker_id != robot.id:
                wait_for[robot.id] = blocker_id

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
    # Atomic swap
    # ------------------------------------------------------------------

    def swap_cells(
        self,
        robot_a_id: uuid.UUID,
        cell_a: tuple[int, int],
        robot_b_id: uuid.UUID,
        cell_b: tuple[int, int],
    ) -> bool:
        """Atomically swap the position of two robots."""
        if self._position.get(cell_a) != robot_a_id:
            return False
        if self._position.get(cell_b) != robot_b_id:
            return False
        self._position[cell_a] = robot_b_id
        self._position[cell_b] = robot_a_id
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def occupied_cells(self) -> dict[tuple[int, int], uuid.UUID]:
        """Read-only snapshot of ALL occupied cells (position + forward)."""
        result = dict(self._position)
        result.update(self._forward)
        return result

    @property
    def position_cells(self) -> dict[tuple[int, int], uuid.UUID]:
        """Read-only snapshot of current physical positions only."""
        return dict(self._position)

    @property
    def forward_cells(self) -> dict[tuple[int, int], uuid.UUID]:
        """Read-only snapshot of forward reservations only."""
        return dict(self._forward)

    def get_cell_block_info(
        self, row: int, col: int, requesting_robot_id: uuid.UUID,
    ) -> dict:
        """Return diagnostic info about why a cell is blocked.

        Returns dict with:
          - blocked_reason: OCCUPIED | RESERVED | FREE
          - blocked_by_rid: str(UUID) | None
          - reservation_age_ticks: int | None
        """
        key = (row, col)
        # Check position first
        pos_occ = self._position.get(key)
        if pos_occ is not None and pos_occ != requesting_robot_id:
            return {
                "blocked_reason": "OCCUPIED",
                "blocked_by_rid": str(pos_occ),
                "reservation_age_ticks": None,
            }
        # Check forward
        fwd_occ = self._forward.get(key)
        if fwd_occ is not None and fwd_occ != requesting_robot_id:
            age = self._current_tick - self._forward_tick.get(key, self._current_tick)
            return {
                "blocked_reason": "RESERVED",
                "blocked_by_rid": str(fwd_occ),
                "reservation_age_ticks": age,
            }
        return {"blocked_reason": "FREE", "blocked_by_rid": None, "reservation_age_ticks": None}

    def force_release_stale(self, row: int, col: int, expected_owner: uuid.UUID) -> bool:
        """Force-release a stale FORWARD reservation.

        Only operates on _forward (never _position).
        Returns True if released, False if not found or not owned by expected_owner.
        """
        key = (row, col)
        if self._forward.get(key) != expected_owner:
            return False
        del self._forward[key]
        self._forward_tick.pop(key, None)
        return True

    def release_position(self, row: int, col: int, robot_id: uuid.UUID) -> bool:
        """Release a POSITION entry.  Use only after confirming robot will move.

        Returns True if released.
        """
        key = (row, col)
        if self._position.get(key) != robot_id:
            return False
        del self._position[key]
        return True
