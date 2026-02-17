"""Application service for Station management."""

from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.models import Station
from src.wes.infrastructure.repositories import PickTaskRepository, StationRepository

logger = logging.getLogger(__name__)


class StationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StationRepository(session)
        self._pick_task_repo = PickTaskRepository(session)

    async def get_station(self, station_id: uuid.UUID) -> Station:
        station = await self._repo.get(station_id)
        if station is None:
            raise ValueError(f"Station {station_id} not found")
        return station

    async def list_stations(
        self, zone_id: uuid.UUID | None = None
    ) -> list[Station]:
        rows = await self._repo.list(zone_id=zone_id)
        return list(rows)

    async def set_online(self, station_id: uuid.UUID, online: bool) -> Station:
        station = await self.get_station(station_id)
        station.is_online = online
        await self._repo.update(station)
        await self._session.commit()
        logger.info(
            "Station %s set %s", station_id, "online" if online else "offline"
        )
        return station

    async def get_queue_count(self, station_id: uuid.UUID) -> int:
        """Return the number of active (non-completed) pick tasks for this station."""
        return await self._pick_task_repo.count_active_for_station(station_id)
