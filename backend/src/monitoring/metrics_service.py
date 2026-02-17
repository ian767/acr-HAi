import time
from dataclasses import dataclass, field
from collections import deque


@dataclass
class KPISnapshot:
    timestamp: float
    orders_completed: int = 0
    orders_in_progress: int = 0
    picks_per_hour: float = 0.0
    robot_utilization: float = 0.0
    avg_pick_time_s: float = 0.0


class MetricsService:
    """Collects and calculates operational KPI metrics."""

    def __init__(self, window_seconds: int = 3600) -> None:
        self._window = window_seconds
        self._pick_times: deque[float] = deque(maxlen=1000)
        self._picks_count: int = 0
        self._orders_completed: int = 0
        self._orders_in_progress: int = 0
        self._robot_count: int = 0
        self._robots_busy: int = 0
        self._start_time: float = time.time()

    def record_pick(self, duration_s: float) -> None:
        self._pick_times.append(duration_s)
        self._picks_count += 1

    def record_order_completed(self) -> None:
        self._orders_completed += 1

    def set_orders_in_progress(self, count: int) -> None:
        self._orders_in_progress = count

    def set_robot_counts(self, total: int, busy: int) -> None:
        self._robot_count = total
        self._robots_busy = busy

    def get_snapshot(self) -> KPISnapshot:
        elapsed = max(time.time() - self._start_time, 1.0)
        hours = elapsed / 3600

        avg_pick = 0.0
        if self._pick_times:
            avg_pick = sum(self._pick_times) / len(self._pick_times)

        utilization = 0.0
        if self._robot_count > 0:
            utilization = self._robots_busy / self._robot_count

        return KPISnapshot(
            timestamp=time.time(),
            orders_completed=self._orders_completed,
            orders_in_progress=self._orders_in_progress,
            picks_per_hour=self._picks_count / hours if hours > 0 else 0,
            robot_utilization=utilization,
            avg_pick_time_s=avg_pick,
        )

    def reset(self) -> None:
        self._pick_times.clear()
        self._picks_count = 0
        self._orders_completed = 0
        self._orders_in_progress = 0
        self._start_time = time.time()


metrics_service = MetricsService()
