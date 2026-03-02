"""A* path-finding on a 2-D cell grid.

Pure computation -- no async, no DB access.
"""

from __future__ import annotations

import heapq
from typing import Sequence

from src.ess.domain.enums import CellType, RobotType

# Cells that robots cannot traverse (per robot type).
# A42TD cannot drive through RACK cells; K50H (small picker) can pass
# through cantilever/rack structures.
_IMPASSABLE_DEFAULT: frozenset[CellType] = frozenset({CellType.WALL, CellType.RACK})
_IMPASSABLE_K50H: frozenset[CellType] = frozenset({CellType.WALL})

# Extra movement cost when K50H traverses a RACK cell.
# Very low so K50H strongly prefers cutting through racks over
# detouring via narrow aisle corridors.
_RACK_TRAVERSAL_COST: float = 0.1

# Extra cost for K50H traversing aisle-row FLOOR cells (narrow corridors
# between rack pairs).  K50H should prefer rack traversal over aisles.
_AISLE_ROW_COST: float = 5.0

# Extra cost for A42TD traversing non-aisle FLOOR cells.
# A42TD should prefer staying in aisles (between rack rows).
_A42TD_NON_AISLE_COST: float = 3.0

# Four-directional movement deltas: (d_row, d_col).
_DIRECTIONS: list[tuple[int, int]] = [(-1, 0), (1, 0), (0, -1), (0, 1)]


class PathPlanner:
    """A* planner over a rectangular grid of :class:`CellType` values.

    Parameters
    ----------
    grid:
        2-D list where ``grid[row][col]`` is a :class:`CellType`.
    congestion:
        Optional mapping from ``(row, col)`` to an additive cost
        representing current traffic congestion on that cell.
    robot_type:
        Optional robot type.  When :attr:`RobotType.K50H`, RACK cells
        are passable with a cost penalty.  Defaults to ``None``
        (A42TD / legacy behaviour where RACK is impassable).
    avoid_cells:
        Optional set of ``(row, col)`` that should be heavily penalised
        (but not blocked).  Used to steer robots away from queue areas
        so only FIFO-bound robots enter them.
    """

    # Cost applied to avoid_cells — high enough that A* will route around
    # them, but not infinity so the planner still succeeds when the goal
    # is inside the zone.
    _AVOID_COST: float = 500.0

    def __init__(
        self,
        grid: list[list[CellType]],
        congestion: dict[tuple[int, int], float] | None = None,
        robot_type: RobotType | None = None,
        aisle_rows: set[int] | None = None,
        territory_cols: tuple[int, int] | None = None,
        territory_rows: tuple[int, int] | None = None,
        avoid_cells: set[tuple[int, int]] | None = None,
    ) -> None:
        self._grid = grid
        self._rows = len(grid)
        self._cols = len(grid[0]) if self._rows > 0 else 0
        self._congestion: dict[tuple[int, int], float] = congestion or {}
        self._robot_type = robot_type
        self._aisle_rows: set[int] = aisle_rows or set()
        # A42TD territory: rectangular grid bounds.
        self._territory_cols = territory_cols
        self._territory_rows = territory_rows
        self._avoid_cells: set[tuple[int, int]] = avoid_cells or set()
        self._impassable = (
            _IMPASSABLE_K50H if robot_type == RobotType.K50H else _IMPASSABLE_DEFAULT
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_path(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """Return an ordered list of ``(row, col)`` waypoints from *start* to *goal*.

        Returns an empty list when no feasible path exists.
        """
        if not self._in_bounds(start) or not self._in_bounds(goal):
            return []
        if self._is_blocked(start):
            return []
        # Auto-resolve blocked goal (e.g. RACK cell) to nearest walkable cell.
        if self._is_blocked(goal):
            resolved = self._nearest_walkable(goal)
            if resolved is None:
                return []
            goal = resolved
        if start == goal:
            return [start]

        # A* open set: (f_score, counter, (row, col))
        counter = 0
        open_set: list[tuple[float, int, tuple[int, int]]] = []
        heapq.heappush(open_set, (self._heuristic(start, goal), counter, start))

        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], float] = {start: 0.0}

        while open_set:
            _, _, current = heapq.heappop(open_set)

            if current == goal:
                return self._reconstruct(came_from, current)

            for dr, dc in _DIRECTIONS:
                neighbour = (current[0] + dr, current[1] + dc)
                if not self._in_bounds(neighbour) or self._is_blocked(neighbour):
                    continue
                # A42TD territory: block cells outside the assigned rectangle.
                if (
                    self._territory_cols is not None
                    and not (self._territory_cols[0] <= neighbour[1] <= self._territory_cols[1])
                ):
                    continue
                if (
                    self._territory_rows is not None
                    and not (self._territory_rows[0] <= neighbour[0] <= self._territory_rows[1])
                ):
                    continue

                move_cost = 1.0 + self._congestion.get(neighbour, 0.0)
                # Avoid-cell penalty (queue zones etc.)
                if neighbour in self._avoid_cells and neighbour != goal:
                    move_cost += self._AVOID_COST
                # K50H pays extra cost for traversing RACK cells.
                if (
                    self._robot_type == RobotType.K50H
                    and self._grid[neighbour[0]][neighbour[1]] == CellType.RACK
                ):
                    move_cost += _RACK_TRAVERSAL_COST
                # K50H: penalize aisle rows so it prefers rack traversal.
                if (
                    self._robot_type == RobotType.K50H
                    and neighbour[0] in self._aisle_rows
                ):
                    move_cost += _AISLE_ROW_COST
                # A42TD: penalize non-aisle FLOOR cells to prefer aisles.
                if (
                    self._robot_type == RobotType.A42TD
                    and self._aisle_rows
                    and neighbour[0] not in self._aisle_rows
                    and self._grid[neighbour[0]][neighbour[1]] == CellType.FLOOR
                ):
                    move_cost += _A42TD_NON_AISLE_COST
                tentative_g = g_score[current] + move_cost

                if tentative_g < g_score.get(neighbour, float("inf")):
                    came_from[neighbour] = current
                    g_score[neighbour] = tentative_g
                    f_score = tentative_g + self._heuristic(neighbour, goal)
                    counter += 1
                    heapq.heappush(open_set, (f_score, counter, neighbour))

        # No path found.
        return []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
        """Manhattan distance heuristic."""
        return float(abs(a[0] - b[0]) + abs(a[1] - b[1]))

    def _nearest_walkable(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        """Find the nearest walkable cell to a blocked position (e.g. RACK)."""
        for dist in range(1, max(self._rows, self._cols)):
            for dr in range(-dist, dist + 1):
                dc_abs = dist - abs(dr)
                for dc in ([dc_abs, -dc_abs] if dc_abs else [0]):
                    nr, nc = pos[0] + dr, pos[1] + dc
                    candidate = (nr, nc)
                    if self._in_bounds(candidate) and not self._is_blocked(candidate):
                        return candidate
        return None

    def _in_bounds(self, pos: tuple[int, int]) -> bool:
        r, c = pos
        return 0 <= r < self._rows and 0 <= c < self._cols

    def _is_blocked(self, pos: tuple[int, int]) -> bool:
        return self._grid[pos[0]][pos[1]] in self._impassable

    @staticmethod
    def _reconstruct(
        came_from: dict[tuple[int, int], tuple[int, int]],
        current: tuple[int, int],
    ) -> list[tuple[int, int]]:
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path
