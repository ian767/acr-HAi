"""In-memory tracker for tote origin positions (allocated vs completed).

Provides lightweight counters to visualize which rack cells totes are
being picked from, without touching the database on every event.
"""

from __future__ import annotations

from collections import defaultdict


class ToteOriginTracker:
    """Track tote allocation and completion counts by grid cell."""

    def __init__(self) -> None:
        self._allocated: dict[tuple[int, int], int] = defaultdict(int)
        self._completed: dict[tuple[int, int], int] = defaultdict(int)
        # Cache task_id -> origin cell so completed step needs no DB hit.
        self._task_origin: dict[str, tuple[int, int]] = {}

    def record_allocated(self, task_id: str, row: int, col: int) -> None:
        """Record that a tote retrieval was allocated from (row, col)."""
        self._allocated[(row, col)] += 1
        self._task_origin[task_id] = (row, col)

    def record_completed_by_task(self, task_id: str) -> None:
        """Record completion using cached origin — no DB hit needed."""
        origin = self._task_origin.pop(task_id, None)
        if origin:
            self._completed[origin] += 1

    def get_allocated_map(self) -> dict[tuple[int, int], int]:
        return dict(self._allocated)

    def get_completed_map(self) -> dict[tuple[int, int], int]:
        return dict(self._completed)

    def reset(self) -> None:
        self._allocated.clear()
        self._completed.clear()
        self._task_origin.clear()


_tracker = ToteOriginTracker()


def get_tracker() -> ToteOriginTracker:
    return _tracker


def reset_tracker() -> None:
    _tracker.reset()
