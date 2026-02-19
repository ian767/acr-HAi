"""Orchestrates equipment tasks: retrieve and return tote flows."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import (
    EquipmentTaskState,
    EquipmentTaskType,
    RobotType,
)
from src.ess.domain.models import EquipmentTask, Location


# Valid state transitions keyed by (current_state, event_name).
_TRANSITIONS: dict[tuple[EquipmentTaskState, str], EquipmentTaskState] = {
    (EquipmentTaskState.PENDING, "a42td_dispatched"): EquipmentTaskState.A42TD_MOVING,
    (EquipmentTaskState.A42TD_MOVING, "at_cantilever"): EquipmentTaskState.AT_CANTILEVER,
    (EquipmentTaskState.AT_CANTILEVER, "k50h_dispatched"): EquipmentTaskState.K50H_MOVING,
    (EquipmentTaskState.K50H_MOVING, "delivered"): EquipmentTaskState.DELIVERED,
    (EquipmentTaskState.DELIVERED, "completed"): EquipmentTaskState.COMPLETED,
}


class TaskExecutor:
    """Coordinates the two-robot retrieve/return workflow.

    The retrieve flow:
        1. Create an :class:`EquipmentTask` with type RETRIEVE.
        2. Find and assign the nearest idle A42TD to carry the tote from
           the rack to the cantilever.
        3. Find and assign the nearest idle K50H to carry the tote from
           the cantilever to the station.

    The return flow mirrors this in the opposite direction.
    """

    def __init__(
        self,
        session: AsyncSession,
        fleet_manager: FleetManager,
        path_planner: PathPlanner,
        traffic_controller: TrafficController,
    ) -> None:
        self._session = session
        self._fleet = fleet_manager
        self._planner = path_planner
        self._traffic = traffic_controller

    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    async def execute_retrieve(
        self,
        pick_task_id: uuid.UUID,
        tote_id: uuid.UUID,
        source_location_id: uuid.UUID,
        station_id: uuid.UUID,
    ) -> EquipmentTask:
        """Start a RETRIEVE equipment task.

        Steps
        -----
        1. Create the ``EquipmentTask`` record.
        2. Look up the source location to determine zone & grid position.
        3. Find the nearest idle A42TD and assign it (rack -> cantilever leg).
        4. Find the nearest idle K50H and assign it (cantilever -> station leg).
        """
        # Fetch source location for grid coordinates and zone.
        source_loc = await self._session.get(Location, source_location_id)
        if source_loc is None:
            raise ValueError(f"Location {source_location_id} not found")

        # Create equipment task.
        task = EquipmentTask(
            pick_task_id=pick_task_id,
            type=EquipmentTaskType.RETRIEVE,
            source_location_id=source_location_id,
            state=EquipmentTaskState.PENDING,
        )
        self._session.add(task)
        await self._session.flush()

        # Assign A42TD (rack -> cantilever).
        a42td = await self._fleet.find_nearest_idle(
            zone_id=source_loc.zone_id,
            robot_type=RobotType.A42TD,
            target_row=source_loc.grid_row,
            target_col=source_loc.grid_col,
        )
        if a42td is not None:
            await self._fleet.assign_robot(a42td.id, task.id)
            task.a42td_robot_id = a42td.id

        # Assign K50H (cantilever -> station).
        k50h = await self._fleet.find_nearest_idle(
            zone_id=source_loc.zone_id,
            robot_type=RobotType.K50H,
            target_row=source_loc.grid_row,
            target_col=source_loc.grid_col,
        )
        if k50h is not None:
            await self._fleet.assign_robot(k50h.id, task.id)
            task.k50h_robot_id = k50h.id

        await self._session.flush()
        return task

    # ------------------------------------------------------------------
    # Return
    # ------------------------------------------------------------------

    async def execute_return(
        self,
        pick_task_id: uuid.UUID,
        tote_id: uuid.UUID,
        target_location_id: uuid.UUID,
        station_id: uuid.UUID,
    ) -> EquipmentTask:
        """Start a RETURN equipment task (reverse of retrieve).

        Steps
        -----
        1. Create the ``EquipmentTask`` record.
        2. Look up the target location to determine zone & grid position.
        3. Find the nearest idle K50H and assign it (station -> cantilever leg).
        4. Find the nearest idle A42TD and assign it (cantilever -> rack leg).
        """
        target_loc = await self._session.get(Location, target_location_id)
        if target_loc is None:
            raise ValueError(f"Location {target_location_id} not found")

        task = EquipmentTask(
            pick_task_id=pick_task_id,
            type=EquipmentTaskType.RETURN,
            target_location_id=target_location_id,
            state=EquipmentTaskState.PENDING,
        )
        self._session.add(task)
        await self._session.flush()

        # Assign K50H (station -> cantilever).
        k50h = await self._fleet.find_nearest_idle(
            zone_id=target_loc.zone_id,
            robot_type=RobotType.K50H,
            target_row=target_loc.grid_row,
            target_col=target_loc.grid_col,
        )
        if k50h is not None:
            await self._fleet.assign_robot(k50h.id, task.id)
            task.k50h_robot_id = k50h.id

        # Assign A42TD (cantilever -> rack).
        a42td = await self._fleet.find_nearest_idle(
            zone_id=target_loc.zone_id,
            robot_type=RobotType.A42TD,
            target_row=target_loc.grid_row,
            target_col=target_loc.grid_col,
        )
        if a42td is not None:
            await self._fleet.assign_robot(a42td.id, task.id)
            task.a42td_robot_id = a42td.id

        await self._session.flush()
        return task

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def advance_task(self, task_id: uuid.UUID, event: str) -> EquipmentTask:
        """Advance an equipment task through its state machine.

        Parameters
        ----------
        task_id:
            Primary key of the :class:`EquipmentTask`.
        event:
            One of ``"a42td_dispatched"``, ``"at_cantilever"``,
            ``"k50h_dispatched"``, ``"delivered"``, ``"completed"``.

        Raises
        ------
        ValueError
            If the transition is invalid for the current state.
        """
        task = await self._session.get(EquipmentTask, task_id)
        if task is None:
            raise ValueError(f"EquipmentTask {task_id} not found")

        key = (task.state, event)
        next_state = _TRANSITIONS.get(key)
        if next_state is None:
            raise ValueError(
                f"Invalid transition: state={task.state.value}, event={event}"
            )

        task.state = next_state

        # Release robots when the task completes (task-safe: only releases
        # robots still assigned to this specific task).
        # For RETRIEVE tasks, do NOT release K50H here — it holds the tote
        # at the station and will be released later by the return flow.
        if next_state == EquipmentTaskState.COMPLETED:
            if task.a42td_robot_id is not None:
                await self._fleet.release_robot(task.a42td_robot_id, task.id)
            if (
                task.k50h_robot_id is not None
                and task.type != EquipmentTaskType.RETRIEVE
            ):
                await self._fleet.release_robot(task.k50h_robot_id, task.id)

        await self._session.flush()
        return task
