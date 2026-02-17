"""SQLAlchemy repository classes for ESS domain models."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.domain.enums import (
    EquipmentTaskState,
    RobotStatus,
    ToteStatus,
)
from src.ess.domain.models import (
    EquipmentTask,
    Location,
    Robot,
    Tote,
    Zone,
)


# ---------------------------------------------------------------------------
# Robot
# ---------------------------------------------------------------------------


class RobotRepository:
    """CRUD and query helpers for :class:`Robot`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, robot: Robot) -> Robot:
        self._session.add(robot)
        await self._session.flush()
        return robot

    async def get(self, robot_id: uuid.UUID) -> Robot | None:
        return await self._session.get(Robot, robot_id)

    async def list_all(self) -> list[Robot]:
        result = await self._session.execute(select(Robot))
        return list(result.scalars().all())

    async def filter_by_zone(self, zone_id: uuid.UUID) -> list[Robot]:
        stmt = select(Robot).where(Robot.zone_id == zone_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def filter_by_status(self, status: RobotStatus) -> list[Robot]:
        stmt = select(Robot).where(Robot.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def filter_by_zone_and_status(
        self, zone_id: uuid.UUID, status: RobotStatus
    ) -> list[Robot]:
        stmt = select(Robot).where(
            Robot.zone_id == zone_id, Robot.status == status
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, robot: Robot) -> Robot:
        await self._session.flush()
        return robot

    async def delete(self, robot_id: uuid.UUID) -> None:
        robot = await self.get(robot_id)
        if robot is not None:
            await self._session.delete(robot)
            await self._session.flush()


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


class ZoneRepository:
    """CRUD helpers for :class:`Zone`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, zone: Zone) -> Zone:
        self._session.add(zone)
        await self._session.flush()
        return zone

    async def get(self, zone_id: uuid.UUID) -> Zone | None:
        return await self._session.get(Zone, zone_id)

    async def list_all(self) -> list[Zone]:
        result = await self._session.execute(select(Zone))
        return list(result.scalars().all())

    async def update(self, zone: Zone) -> Zone:
        await self._session.flush()
        return zone

    async def delete(self, zone_id: uuid.UUID) -> None:
        zone = await self.get(zone_id)
        if zone is not None:
            await self._session.delete(zone)
            await self._session.flush()


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class LocationRepository:
    """CRUD and query helpers for :class:`Location`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, location: Location) -> Location:
        self._session.add(location)
        await self._session.flush()
        return location

    async def get(self, location_id: uuid.UUID) -> Location | None:
        return await self._session.get(Location, location_id)

    async def list_all(self) -> list[Location]:
        result = await self._session.execute(select(Location))
        return list(result.scalars().all())

    async def filter_by_zone(self, zone_id: uuid.UUID) -> list[Location]:
        stmt = select(Location).where(Location.zone_id == zone_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, location: Location) -> Location:
        await self._session.flush()
        return location

    async def delete(self, location_id: uuid.UUID) -> None:
        location = await self.get(location_id)
        if location is not None:
            await self._session.delete(location)
            await self._session.flush()


# ---------------------------------------------------------------------------
# Tote
# ---------------------------------------------------------------------------


class ToteRepository:
    """CRUD and query helpers for :class:`Tote`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tote: Tote) -> Tote:
        self._session.add(tote)
        await self._session.flush()
        return tote

    async def get(self, tote_id: uuid.UUID) -> Tote | None:
        return await self._session.get(Tote, tote_id)

    async def list_all(self) -> list[Tote]:
        result = await self._session.execute(select(Tote))
        return list(result.scalars().all())

    async def filter_by_status(self, status: ToteStatus) -> list[Tote]:
        stmt = select(Tote).where(Tote.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, tote: Tote) -> Tote:
        await self._session.flush()
        return tote

    async def delete(self, tote_id: uuid.UUID) -> None:
        tote = await self.get(tote_id)
        if tote is not None:
            await self._session.delete(tote)
            await self._session.flush()


# ---------------------------------------------------------------------------
# EquipmentTask
# ---------------------------------------------------------------------------


class EquipmentTaskRepository:
    """CRUD and query helpers for :class:`EquipmentTask`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, task: EquipmentTask) -> EquipmentTask:
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, task_id: uuid.UUID) -> EquipmentTask | None:
        return await self._session.get(EquipmentTask, task_id)

    async def list_all(self) -> list[EquipmentTask]:
        result = await self._session.execute(select(EquipmentTask))
        return list(result.scalars().all())

    async def filter_by_state(
        self, state: EquipmentTaskState
    ) -> list[EquipmentTask]:
        stmt = select(EquipmentTask).where(EquipmentTask.state == state)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, task: EquipmentTask) -> EquipmentTask:
        await self._session.flush()
        return task

    async def delete(self, task_id: uuid.UUID) -> None:
        task = await self.get(task_id)
        if task is not None:
            await self._session.delete(task)
            await self._session.flush()
