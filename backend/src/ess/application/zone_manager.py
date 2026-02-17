"""Zone management: CRUD and grid construction."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.domain.enums import CellType
from src.ess.domain.models import Zone


class ZoneManager:
    """Manages warehouse zones and their grid layouts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_zone(self, name: str, rows: int, cols: int) -> Zone:
        """Create and persist a new zone."""
        zone = Zone(name=name, grid_rows=rows, grid_cols=cols)
        self._session.add(zone)
        await self._session.flush()
        return zone

    async def get_zone(self, zone_id: uuid.UUID) -> Zone:
        """Fetch a zone by its primary key."""
        zone = await self._session.get(Zone, zone_id)
        if zone is None:
            raise ValueError(f"Zone {zone_id} not found")
        return zone

    async def list_zones(self) -> list[Zone]:
        """Return all zones."""
        result = await self._session.execute(select(Zone))
        return list(result.scalars().all())

    async def build_grid(
        self,
        zone_id: uuid.UUID,
        config: dict,
    ) -> list[list[CellType]]:
        """Build a 2-D grid for the zone from a configuration dictionary.

        The *config* dictionary is expected to have the following shape::

            {
                "walls": [[row, col], ...],
                "racks": [[row, col], ...],
                "cantilevers": [[row, col], ...],
                "stations": [[row, col], ...],
                "aisles": [[row, col], ...],
                "charging": [[row, col], ...],
            }

        Any cell not explicitly listed defaults to :attr:`CellType.FLOOR`.
        """
        zone = await self.get_zone(zone_id)
        rows, cols = zone.grid_rows, zone.grid_cols

        grid: list[list[CellType]] = [
            [CellType.FLOOR for _ in range(cols)] for _ in range(rows)
        ]

        _TYPE_KEYS: dict[str, CellType] = {
            "walls": CellType.WALL,
            "racks": CellType.RACK,
            "cantilevers": CellType.CANTILEVER,
            "stations": CellType.STATION,
            "aisles": CellType.AISLE,
            "charging": CellType.CHARGING,
        }

        for key, cell_type in _TYPE_KEYS.items():
            for cell in config.get(key, []):
                r, c = int(cell[0]), int(cell[1])
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = cell_type

        return grid
