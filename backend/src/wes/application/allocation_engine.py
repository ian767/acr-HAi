"""Score-based allocation engine.

Selects the best station for an order by evaluating four weighted criteria:
  - station queue capacity  (weight 0.3)
  - same-SKU batching       (weight 0.3)
  - PBT urgency             (weight 0.2)
  - available robots in zone (weight 0.2)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.domain.enums import RobotStatus
from src.ess.domain.models import Robot
from src.wes.domain.enums import PickTaskState
from src.wes.domain.models import Order, PickTask, Station


# Module-level allocation counters — persist across requests within
# the same process lifetime.  Reset on simulation restart.
_allocation_counts: dict[str, int] = {}  # station_id → total orders
_last_scores: dict[str, dict] = {}       # station_id → {name, score}


def get_allocation_stats() -> dict:
    """Return current allocation distribution stats (no DB query)."""
    total = sum(_allocation_counts.values()) or 1
    stations = []
    for sid, count in _allocation_counts.items():
        entry: dict = {
            "station_id": sid,
            "count": count,
            "pct": round(count / total * 100, 1),
        }
        info = _last_scores.get(sid)
        if info:
            entry["name"] = info.get("name", sid[:8])
            entry["last_score"] = round(info.get("score", 0.0), 3)
        stations.append(entry)
    stations.sort(key=lambda x: x["count"], reverse=True)
    return {"total": sum(_allocation_counts.values()), "stations": stations}


def reset_allocation_stats() -> None:
    """Clear counters (call on simulation reset)."""
    _allocation_counts.clear()
    _last_scores.clear()


class AllocationEngine:
    # Scoring weights
    W_QUEUE = 0.3
    W_BATCH = 0.3
    W_PBT = 0.2
    W_ROBOTS = 0.2

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def allocate(self, order: Order) -> uuid.UUID:
        """Return the station_id with the highest composite score.

        Raises ``RuntimeError`` when no eligible stations exist.
        """
        if order.zone_id is None:
            raise RuntimeError("Cannot allocate: no zone. Apply a preset first.")
        stations = await self._online_stations(order.zone_id)
        if not stations:
            raise RuntimeError("No online stations available for allocation")

        best_station: Station | None = None
        best_score: float = -1.0

        for station in stations:
            score = await self._score(station, order)
            if score > best_score:
                best_score = score
                best_station = station

        assert best_station is not None
        # Track allocation stats (in-memory, no DB)
        sid = str(best_station.id)
        _allocation_counts[sid] = _allocation_counts.get(sid, 0) + 1
        _last_scores[sid] = {"name": best_station.name, "score": best_score}
        return best_station.id

    # ------------------------------------------------------------------
    # Internal scoring helpers
    # ------------------------------------------------------------------

    async def _score(self, station: Station, order: Order) -> float:
        queue_score = await self._queue_score(station)
        batch_score = await self._batch_score(station, order.sku)
        pbt_score = self._pbt_score(order)
        robot_score = await self._robot_score(station.zone_id)
        return (
            self.W_QUEUE * queue_score
            + self.W_BATCH * batch_score
            + self.W_PBT * pbt_score
            + self.W_ROBOTS * robot_score
        )

    async def _queue_score(self, station: Station) -> float:
        """Higher score for stations with more free queue slots."""
        stmt = (
            select(func.count())
            .select_from(PickTask)
            .where(
                PickTask.station_id == station.id,
                PickTask.state != PickTaskState.COMPLETED,
            )
        )
        result = await self._session.execute(stmt)
        active = result.scalar_one()
        if station.max_queue_size == 0:
            return 0.0
        return max(0.0, 1.0 - active / station.max_queue_size)

    async def _batch_score(self, station: Station, sku: str) -> float:
        """Higher score when the station already has tasks for the same SKU."""
        stmt = (
            select(func.count())
            .select_from(PickTask)
            .where(
                PickTask.station_id == station.id,
                PickTask.sku == sku,
                PickTask.state != PickTaskState.COMPLETED,
            )
        )
        result = await self._session.execute(stmt)
        same_sku = result.scalar_one()
        # Normalise: cap at 1.0 when there are 3+ same-SKU tasks
        return min(1.0, same_sku / 3.0) if same_sku > 0 else 0.0

    @staticmethod
    def _pbt_score(order: Order) -> float:
        """Higher score for orders with an imminent pick-before time."""
        if order.pbt_at is None:
            return 0.0
        now = datetime.now(timezone.utc)
        remaining = (order.pbt_at - now).total_seconds()
        if remaining <= 0:
            return 1.0
        # Within 1 hour -> high urgency; beyond 4 hours -> low
        four_hours = 4 * 3600.0
        return max(0.0, 1.0 - remaining / four_hours)

    async def _robot_score(self, zone_id: uuid.UUID) -> float:
        """Higher score when more idle robots are available in the zone."""
        stmt = (
            select(func.count())
            .select_from(Robot)
            .where(Robot.zone_id == zone_id, Robot.status == RobotStatus.IDLE)
        )
        result = await self._session.execute(stmt)
        idle_robots = result.scalar_one()
        # Normalise: cap at 1.0 when there are 4+ idle robots
        return min(1.0, idle_robots / 4.0) if idle_robots > 0 else 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _online_stations(
        self, zone_id: uuid.UUID | None
    ) -> Sequence[Station]:
        stmt = select(Station).where(Station.is_online.is_(True))
        if zone_id is not None:
            stmt = stmt.where(Station.zone_id == zone_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()
