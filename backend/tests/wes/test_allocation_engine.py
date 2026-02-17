"""Unit tests for the AllocationEngine (mocked database layer)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.wes.application.allocation_engine import AllocationEngine
from src.wes.domain.enums import OrderStatus, PickTaskState, StationStatus
from src.wes.domain.models import Order, Station


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_station(
    *,
    name: str = "S1",
    zone_id: uuid.UUID | None = None,
    is_online: bool = True,
    max_queue_size: int = 6,
) -> Station:
    station = MagicMock(spec=Station)
    station.id = uuid.uuid4()
    station.name = name
    station.zone_id = zone_id or uuid.uuid4()
    station.is_online = is_online
    station.max_queue_size = max_queue_size
    station.status = StationStatus.IDLE
    station.grid_row = 0
    station.grid_col = 0
    return station


def _make_order(
    *,
    sku: str = "SKU-001",
    quantity: int = 5,
    priority: int = 0,
    zone_id: uuid.UUID | None = None,
    pbt_at: datetime | None = None,
) -> Order:
    order = MagicMock(spec=Order)
    order.id = uuid.uuid4()
    order.external_id = "EXT-001"
    order.sku = sku
    order.quantity = quantity
    order.priority = priority
    order.zone_id = zone_id or uuid.uuid4()
    order.pbt_at = pbt_at
    order.status = OrderStatus.NEW
    order.station_id = None
    return order


def _mock_execute_scalar(value: int):
    """Return an AsyncMock that simulates session.execute() -> result.scalar_one()."""
    result = MagicMock()
    result.scalar_one.return_value = value
    execute = AsyncMock(return_value=result)
    return execute


def _mock_execute_scalars(rows: list):
    """Return an AsyncMock that simulates session.execute() -> result.scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    execute = AsyncMock(return_value=result)
    return execute


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAllocationEngineNoStations:
    """When there are no online stations the engine must raise."""

    @pytest.mark.asyncio
    async def test_raises_when_no_stations(self):
        session = AsyncMock()
        # First execute call is _online_stations -> empty
        session.execute = _mock_execute_scalars([])

        engine = AllocationEngine(session)
        order = _make_order()

        with pytest.raises(RuntimeError, match="No online stations"):
            await engine.allocate(order)


class TestAllocationEngineSingleStation:
    """With one station available it must always be selected."""

    @pytest.mark.asyncio
    async def test_returns_only_station(self):
        zone_id = uuid.uuid4()
        station = _make_station(zone_id=zone_id)
        order = _make_order(zone_id=zone_id)

        session = AsyncMock()

        # We need to handle multiple execute calls:
        # 1. _online_stations
        # 2. _queue_score (active pick tasks count)
        # 3. _batch_score (same-sku count)
        # 4. _robot_score (idle robots count)

        call_counter = {"n": 0}
        original_results = [
            # _online_stations -> return [station]
            _mock_execute_scalars([station]),
            # _queue_score -> 0 active tasks
            _mock_execute_scalar(0),
            # _batch_score -> 0 same sku
            _mock_execute_scalar(0),
            # _robot_score -> 2 idle robots
            _mock_execute_scalar(2),
        ]

        async def _side_effect(stmt):
            idx = call_counter["n"]
            call_counter["n"] += 1
            execute_fn = original_results[idx]
            return await execute_fn(stmt)

        session.execute = AsyncMock(side_effect=_side_effect)

        engine = AllocationEngine(session)
        result = await engine.allocate(order)
        assert result == station.id


class TestAllocationEngineScoring:
    """Verify scoring logic picks the better station."""

    @pytest.mark.asyncio
    async def test_prefers_less_loaded_station(self):
        """Station with fewer queued tasks should score higher on queue."""
        zone_id = uuid.uuid4()
        station_a = _make_station(name="A", zone_id=zone_id, max_queue_size=6)
        station_b = _make_station(name="B", zone_id=zone_id, max_queue_size=6)
        order = _make_order(zone_id=zone_id)

        session = AsyncMock()

        call_counter = {"n": 0}
        original_results = [
            # _online_stations -> both stations
            _mock_execute_scalars([station_a, station_b]),
            # Station A scoring:
            _mock_execute_scalar(5),   # queue: 5/6 active -> low score
            _mock_execute_scalar(0),   # batch: no same sku
            _mock_execute_scalar(1),   # robots: 1 idle
            # Station B scoring:
            _mock_execute_scalar(1),   # queue: 1/6 active -> high score
            _mock_execute_scalar(0),   # batch: no same sku
            _mock_execute_scalar(1),   # robots: 1 idle
        ]

        async def _side_effect(stmt):
            idx = call_counter["n"]
            call_counter["n"] += 1
            return await original_results[idx](stmt)

        session.execute = AsyncMock(side_effect=_side_effect)

        engine = AllocationEngine(session)
        result = await engine.allocate(order)
        # Station B has a better queue score (5/6 free vs 1/6 free)
        assert result == station_b.id

    @pytest.mark.asyncio
    async def test_prefers_same_sku_batching(self):
        """Station with same-SKU tasks should score higher on batching."""
        zone_id = uuid.uuid4()
        station_a = _make_station(name="A", zone_id=zone_id, max_queue_size=6)
        station_b = _make_station(name="B", zone_id=zone_id, max_queue_size=6)
        order = _make_order(sku="SKU-MATCH", zone_id=zone_id)

        session = AsyncMock()

        call_counter = {"n": 0}
        original_results = [
            # _online_stations
            _mock_execute_scalars([station_a, station_b]),
            # Station A: empty queue, no batch, some robots
            _mock_execute_scalar(0),   # queue: 0 active
            _mock_execute_scalar(0),   # batch: 0 same sku
            _mock_execute_scalar(2),   # robots: 2 idle
            # Station B: some queue, strong batch, some robots
            _mock_execute_scalar(2),   # queue: 2 active
            _mock_execute_scalar(3),   # batch: 3 same sku -> batch score 1.0
            _mock_execute_scalar(2),   # robots: 2 idle
        ]

        async def _side_effect(stmt):
            idx = call_counter["n"]
            call_counter["n"] += 1
            return await original_results[idx](stmt)

        session.execute = AsyncMock(side_effect=_side_effect)

        engine = AllocationEngine(session)
        result = await engine.allocate(order)
        # Station B has batch score = 1.0 (weight 0.3) which outweighs
        # station A queue advantage of ~0.1 (0.3 * 0.33)
        assert result == station_b.id


class TestAllocationEnginePbtUrgency:
    """Verify PBT urgency scoring."""

    def test_no_pbt_returns_zero(self):
        order = _make_order(pbt_at=None)
        score = AllocationEngine._pbt_score(order)
        assert score == 0.0

    def test_past_pbt_returns_one(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        order = _make_order(pbt_at=past)
        score = AllocationEngine._pbt_score(order)
        assert score == 1.0

    def test_far_future_pbt_returns_low(self):
        far_future = datetime.now(timezone.utc) + timedelta(hours=10)
        order = _make_order(pbt_at=far_future)
        score = AllocationEngine._pbt_score(order)
        assert score == 0.0  # beyond 4-hour window

    def test_imminent_pbt_returns_high(self):
        soon = datetime.now(timezone.utc) + timedelta(minutes=30)
        order = _make_order(pbt_at=soon)
        score = AllocationEngine._pbt_score(order)
        # 30 min remaining out of 4 hours -> ~0.875
        assert 0.8 < score < 1.0


class TestAllocationEngineWeights:
    """Verify weight constants."""

    def test_weights_sum_to_one(self):
        total = (
            AllocationEngine.W_QUEUE
            + AllocationEngine.W_BATCH
            + AllocationEngine.W_PBT
            + AllocationEngine.W_ROBOTS
        )
        assert total == pytest.approx(1.0)
