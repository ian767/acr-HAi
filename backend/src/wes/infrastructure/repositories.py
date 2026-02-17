"""SQLAlchemy async repository classes for WES aggregates."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.enums import OrderStatus, PickTaskState
from src.wes.domain.models import Inventory, Order, PickTask, Station


# ---------------------------------------------------------------------------
# OrderRepository
# ---------------------------------------------------------------------------


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, order: Order) -> Order:
        self._session.add(order)
        await self._session.flush()
        return order

    async def get(self, order_id: uuid.UUID) -> Order | None:
        return await self._session.get(Order, order_id)

    async def list(
        self,
        *,
        status: OrderStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Order]:
        stmt = select(Order).order_by(Order.created_at.desc())
        if status is not None:
            stmt = stmt.where(Order.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, order: Order) -> Order:
        await self._session.flush()
        return order

    async def count_by_status(self, status: OrderStatus) -> int:
        stmt = select(func.count()).select_from(Order).where(Order.status == status)
        result = await self._session.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# PickTaskRepository
# ---------------------------------------------------------------------------


class PickTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: PickTask) -> PickTask:
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, task_id: uuid.UUID) -> PickTask | None:
        return await self._session.get(PickTask, task_id)

    async def list(
        self,
        *,
        station_id: uuid.UUID | None = None,
        state: PickTaskState | None = None,
        order_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[PickTask]:
        stmt = select(PickTask).order_by(PickTask.created_at.desc())
        if station_id is not None:
            stmt = stmt.where(PickTask.station_id == station_id)
        if state is not None:
            stmt = stmt.where(PickTask.state == state)
        if order_id is not None:
            stmt = stmt.where(PickTask.order_id == order_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, task: PickTask) -> PickTask:
        await self._session.flush()
        return task

    async def count_active_for_station(self, station_id: uuid.UUID) -> int:
        """Count non-completed pick tasks for a station."""
        stmt = (
            select(func.count())
            .select_from(PickTask)
            .where(
                PickTask.station_id == station_id,
                PickTask.state != PickTaskState.COMPLETED,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# StationRepository
# ---------------------------------------------------------------------------


class StationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, station: Station) -> Station:
        self._session.add(station)
        await self._session.flush()
        return station

    async def get(self, station_id: uuid.UUID) -> Station | None:
        return await self._session.get(Station, station_id)

    async def list(
        self,
        *,
        zone_id: uuid.UUID | None = None,
        online_only: bool = False,
    ) -> Sequence[Station]:
        stmt = select(Station).order_by(Station.name)
        if zone_id is not None:
            stmt = stmt.where(Station.zone_id == zone_id)
        if online_only:
            stmt = stmt.where(Station.is_online.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, station: Station) -> Station:
        await self._session.flush()
        return station


# ---------------------------------------------------------------------------
# InventoryRepository
# ---------------------------------------------------------------------------


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, inventory: Inventory) -> Inventory:
        self._session.add(inventory)
        await self._session.flush()
        return inventory

    async def get(self, inventory_id: uuid.UUID) -> Inventory | None:
        return await self._session.get(Inventory, inventory_id)

    async def get_by_sku_zone(
        self, sku: str, zone_id: uuid.UUID
    ) -> Inventory | None:
        stmt = select(Inventory).where(
            Inventory.sku == sku, Inventory.zone_id == zone_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list(
        self,
        *,
        sku: str | None = None,
        zone_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Inventory]:
        stmt = select(Inventory)
        if sku is not None:
            stmt = stmt.where(Inventory.sku == sku)
        if zone_id is not None:
            stmt = stmt.where(Inventory.zone_id == zone_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, inventory: Inventory) -> Inventory:
        await self._session.flush()
        return inventory
