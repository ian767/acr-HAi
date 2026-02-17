"""Unit tests for the TaskExecutor (mocked dependencies)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.task_executor import TaskExecutor
from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import (
    CellType,
    EquipmentTaskState,
    EquipmentTaskType,
    RobotStatus,
    RobotType,
)
from src.ess.domain.models import EquipmentTask, Location, Robot


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_robot(
    robot_type: RobotType = RobotType.A42TD,
    status: RobotStatus = RobotStatus.IDLE,
    row: int = 0,
    col: int = 0,
) -> MagicMock:
    robot = MagicMock(spec=Robot)
    robot.id = uuid.uuid4()
    robot.type = robot_type
    robot.status = status
    robot.grid_row = row
    robot.grid_col = col
    robot.zone_id = uuid.uuid4()
    robot.current_task_id = None
    return robot


def _make_location(zone_id: uuid.UUID, row: int = 5, col: int = 5) -> MagicMock:
    loc = MagicMock(spec=Location)
    loc.id = uuid.uuid4()
    loc.zone_id = zone_id
    loc.grid_row = row
    loc.grid_col = col
    return loc


def _make_session() -> AsyncMock:
    """Return a mock AsyncSession with add/flush/get support."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Retrieve flow
# ---------------------------------------------------------------------------


class TestExecuteRetrieve:
    """TaskExecutor.execute_retrieve should create a task and assign robots."""

    @pytest.mark.asyncio
    async def test_creates_equipment_task_and_assigns_robots(self):
        zone_id = uuid.uuid4()
        loc = _make_location(zone_id)
        a42td = _make_robot(RobotType.A42TD, row=1, col=1)
        k50h = _make_robot(RobotType.K50H, row=2, col=2)

        session = _make_session()
        session.get = AsyncMock(return_value=loc)

        fleet = AsyncMock(spec=FleetManager)
        fleet.find_nearest_idle = AsyncMock(
            side_effect=lambda zone_id, robot_type, target_row, target_col: (
                a42td if robot_type == RobotType.A42TD else k50h
            )
        )
        fleet.assign_robot = AsyncMock(
            side_effect=lambda rid, tid: _make_robot()
        )

        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        pick_task_id = uuid.uuid4()
        tote_id = uuid.uuid4()
        station_id = uuid.uuid4()

        task = await executor.execute_retrieve(
            pick_task_id=pick_task_id,
            tote_id=tote_id,
            source_location_id=loc.id,
            station_id=station_id,
        )

        # An EquipmentTask was added to the session.
        session.add.assert_called_once()
        added_task = session.add.call_args[0][0]
        assert isinstance(added_task, EquipmentTask)
        assert added_task.type == EquipmentTaskType.RETRIEVE
        assert added_task.state == EquipmentTaskState.PENDING
        assert added_task.pick_task_id == pick_task_id

        # Fleet manager was asked for both robot types.
        assert fleet.find_nearest_idle.call_count == 2
        assert fleet.assign_robot.call_count == 2

    @pytest.mark.asyncio
    async def test_retrieve_with_no_available_robots(self):
        zone_id = uuid.uuid4()
        loc = _make_location(zone_id)

        session = _make_session()
        session.get = AsyncMock(return_value=loc)

        fleet = AsyncMock(spec=FleetManager)
        fleet.find_nearest_idle = AsyncMock(return_value=None)

        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        task = await executor.execute_retrieve(
            pick_task_id=uuid.uuid4(),
            tote_id=uuid.uuid4(),
            source_location_id=loc.id,
            station_id=uuid.uuid4(),
        )

        # Task still created, but no robots assigned.
        session.add.assert_called_once()
        added_task = session.add.call_args[0][0]
        assert added_task.a42td_robot_id is None
        assert added_task.k50h_robot_id is None
        fleet.assign_robot.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrieve_raises_on_missing_location(self):
        session = _make_session()
        session.get = AsyncMock(return_value=None)

        fleet = AsyncMock(spec=FleetManager)
        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        with pytest.raises(ValueError, match="not found"):
            await executor.execute_retrieve(
                pick_task_id=uuid.uuid4(),
                tote_id=uuid.uuid4(),
                source_location_id=uuid.uuid4(),
                station_id=uuid.uuid4(),
            )


# ---------------------------------------------------------------------------
# Return flow
# ---------------------------------------------------------------------------


class TestExecuteReturn:
    """TaskExecutor.execute_return should create a RETURN task and assign robots."""

    @pytest.mark.asyncio
    async def test_creates_return_task_and_assigns_robots(self):
        zone_id = uuid.uuid4()
        loc = _make_location(zone_id)
        a42td = _make_robot(RobotType.A42TD, row=3, col=3)
        k50h = _make_robot(RobotType.K50H, row=4, col=4)

        session = _make_session()
        session.get = AsyncMock(return_value=loc)

        fleet = AsyncMock(spec=FleetManager)
        fleet.find_nearest_idle = AsyncMock(
            side_effect=lambda zone_id, robot_type, target_row, target_col: (
                k50h if robot_type == RobotType.K50H else a42td
            )
        )
        fleet.assign_robot = AsyncMock(
            side_effect=lambda rid, tid: _make_robot()
        )

        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        pick_task_id = uuid.uuid4()
        tote_id = uuid.uuid4()
        station_id = uuid.uuid4()

        task = await executor.execute_return(
            pick_task_id=pick_task_id,
            tote_id=tote_id,
            target_location_id=loc.id,
            station_id=station_id,
        )

        session.add.assert_called_once()
        added_task = session.add.call_args[0][0]
        assert isinstance(added_task, EquipmentTask)
        assert added_task.type == EquipmentTaskType.RETURN
        assert added_task.state == EquipmentTaskState.PENDING
        assert added_task.pick_task_id == pick_task_id

        assert fleet.find_nearest_idle.call_count == 2
        assert fleet.assign_robot.call_count == 2

    @pytest.mark.asyncio
    async def test_return_raises_on_missing_location(self):
        session = _make_session()
        session.get = AsyncMock(return_value=None)

        fleet = AsyncMock(spec=FleetManager)
        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        with pytest.raises(ValueError, match="not found"):
            await executor.execute_return(
                pick_task_id=uuid.uuid4(),
                tote_id=uuid.uuid4(),
                target_location_id=uuid.uuid4(),
                station_id=uuid.uuid4(),
            )


# ---------------------------------------------------------------------------
# Advance task (state machine)
# ---------------------------------------------------------------------------


class TestAdvanceTask:
    """TaskExecutor.advance_task should progress through valid transitions."""

    @pytest.mark.asyncio
    async def test_valid_transition_pending_to_a42td_moving(self):
        task = EquipmentTask(
            pick_task_id=uuid.uuid4(),
            type=EquipmentTaskType.RETRIEVE,
            state=EquipmentTaskState.PENDING,
        )
        task.id = uuid.uuid4()
        task.a42td_robot_id = None
        task.k50h_robot_id = None

        session = _make_session()
        session.get = AsyncMock(return_value=task)

        fleet = AsyncMock(spec=FleetManager)
        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)
        result = await executor.advance_task(task.id, "a42td_dispatched")

        assert result.state == EquipmentTaskState.A42TD_MOVING

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self):
        task = EquipmentTask(
            pick_task_id=uuid.uuid4(),
            type=EquipmentTaskType.RETRIEVE,
            state=EquipmentTaskState.PENDING,
        )
        task.id = uuid.uuid4()

        session = _make_session()
        session.get = AsyncMock(return_value=task)

        fleet = AsyncMock(spec=FleetManager)
        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)

        with pytest.raises(ValueError, match="Invalid transition"):
            await executor.advance_task(task.id, "delivered")

    @pytest.mark.asyncio
    async def test_completion_releases_robots(self):
        a42td_id = uuid.uuid4()
        k50h_id = uuid.uuid4()

        task = EquipmentTask(
            pick_task_id=uuid.uuid4(),
            type=EquipmentTaskType.RETRIEVE,
            state=EquipmentTaskState.DELIVERED,
        )
        task.id = uuid.uuid4()
        task.a42td_robot_id = a42td_id
        task.k50h_robot_id = k50h_id

        session = _make_session()
        session.get = AsyncMock(return_value=task)

        fleet = AsyncMock(spec=FleetManager)
        fleet.release_robot = AsyncMock()
        planner = MagicMock(spec=PathPlanner)
        traffic = MagicMock(spec=TrafficController)

        executor = TaskExecutor(session, fleet, planner, traffic)
        result = await executor.advance_task(task.id, "completed")

        assert result.state == EquipmentTaskState.COMPLETED
        assert fleet.release_robot.call_count == 2
        fleet.release_robot.assert_any_call(a42td_id)
        fleet.release_robot.assert_any_call(k50h_id)
