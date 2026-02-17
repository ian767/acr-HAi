"""Unit tests for the A* path planner."""

from __future__ import annotations

import pytest

from src.ess.application.path_planner import PathPlanner
from src.ess.domain.enums import CellType

F = CellType.FLOOR
W = CellType.WALL
R = CellType.RACK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_grid(rows: int, cols: int, cell: CellType = F) -> list[list[CellType]]:
    """Create a uniform grid of the given cell type."""
    return [[cell for _ in range(cols)] for _ in range(rows)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFindShortestPath:
    """A* should return the shortest Manhattan path on an open grid."""

    def test_adjacent_cells(self):
        grid = _make_grid(5, 5)
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (0, 1))
        assert path == [(0, 0), (0, 1)]

    def test_same_cell(self):
        grid = _make_grid(5, 5)
        planner = PathPlanner(grid)
        path = planner.find_path((2, 2), (2, 2))
        assert path == [(2, 2)]

    def test_shortest_path_open_grid(self):
        grid = _make_grid(5, 5)
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (4, 4))
        # Optimal length is Manhattan distance + 1 = 8 + 1 = 9 waypoints.
        assert len(path) == 9
        assert path[0] == (0, 0)
        assert path[-1] == (4, 4)
        # Every consecutive pair should differ by exactly one step.
        for a, b in zip(path, path[1:]):
            assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1

    def test_shortest_path_rectangular_grid(self):
        grid = _make_grid(3, 10)
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (2, 9))
        assert len(path) == 12  # 2 + 9 + 1
        assert path[0] == (0, 0)
        assert path[-1] == (2, 9)


class TestPathAvoidsWalls:
    """A* must route around impassable WALL cells."""

    def test_wall_across_middle(self):
        grid = _make_grid(5, 5)
        # Place a wall across row 2, leaving a gap at col 4.
        for c in range(4):
            grid[2][c] = W
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (4, 0))
        assert path  # a path should exist
        assert path[0] == (0, 0)
        assert path[-1] == (4, 0)
        # No waypoint should be on a wall cell.
        for r, c in path:
            assert grid[r][c] != W

    def test_wall_and_rack_impassable(self):
        grid = _make_grid(5, 5)
        # Block col 2 with racks, leaving a gap at row 0.
        for r in range(1, 5):
            grid[r][2] = R
        planner = PathPlanner(grid)
        path = planner.find_path((2, 0), (2, 4))
        assert path
        for r, c in path:
            assert grid[r][c] not in (W, R)


class TestCongestion:
    """Congested cells should have a higher traversal cost."""

    def test_congestion_avoids_costly_cells(self):
        grid = _make_grid(3, 5)
        # Without congestion, path goes straight.
        planner_clean = PathPlanner(grid)
        path_clean = planner_clean.find_path((0, 0), (0, 4))
        assert len(path_clean) == 5  # straight line

        # Add heavy congestion along row 0.
        congestion = {(0, c): 100.0 for c in range(1, 4)}
        planner_cong = PathPlanner(grid, congestion=congestion)
        path_cong = planner_cong.find_path((0, 0), (0, 4))
        # The planner should detour around the congested cells so the path
        # is longer than the straight-line distance.
        assert len(path_cong) > len(path_clean)

    def test_light_congestion_still_reaches_goal(self):
        grid = _make_grid(5, 5)
        congestion = {(r, c): 0.5 for r in range(5) for c in range(5)}
        planner = PathPlanner(grid, congestion=congestion)
        path = planner.find_path((0, 0), (4, 4))
        assert path
        assert path[0] == (0, 0)
        assert path[-1] == (4, 4)


class TestNoPath:
    """When no path exists, find_path should return an empty list."""

    def test_completely_walled_off(self):
        grid = _make_grid(5, 5)
        # Surround (4, 4) with walls.
        for r, c in [(3, 4), (4, 3), (3, 3)]:
            grid[r][c] = W
        grid[4][4] = F  # target is floor but unreachable
        # Also block the last possible entry.
        grid[4][3] = W
        grid[3][4] = W
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (4, 4))
        assert path == []

    def test_start_on_wall(self):
        grid = _make_grid(5, 5)
        grid[0][0] = W
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (4, 4))
        assert path == []

    def test_goal_on_wall(self):
        grid = _make_grid(5, 5)
        grid[4][4] = W
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (4, 4))
        assert path == []

    def test_out_of_bounds(self):
        grid = _make_grid(5, 5)
        planner = PathPlanner(grid)
        path = planner.find_path((0, 0), (10, 10))
        assert path == []
