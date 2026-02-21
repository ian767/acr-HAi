"""A* path-finding on a 2-D cell grid.

Pure computation -- no async, no DB access.
"""

from __future__ import annotations

import heapq
from typing import Sequence

from src.ess.domain.enums import CellType

# Cells that robots cannot traverse.
# A42TD navigates via aisle (FLOOR) rows between rack groups; it cannot drive
# through RACK cells themselves.
_IMPASSABLE: frozenset[CellType] = frozenset({CellType.WALL, CellType.RACK})

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
    """

    def __init__(
        self,
        grid: list[list[CellType]],
        congestion: dict[tuple[int, int], float] | None = None,
    ) -> None:
        self._grid = grid
        self._rows = len(grid)
        self._cols = len(grid[0]) if self._rows > 0 else 0
        self._congestion: dict[tuple[int, int], float] = congestion or {}

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

                move_cost = 1.0 + self._congestion.get(neighbour, 0.0)
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
        return self._grid[pos[0]][pos[1]] in _IMPASSABLE

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
