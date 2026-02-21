"""Application service for inventory management."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.wes.domain.models import Inventory
from src.wes.infrastructure.repositories import InventoryRepository


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = InventoryRepository(session)

    async def get_inventory(self, sku: str, zone_id: uuid.UUID) -> Inventory | None:
        """Look up inventory for a given SKU in a zone."""
        return await self._repo.get_by_sku_zone(sku, zone_id)

    async def _find_inventory(self, sku: str, zone_id: uuid.UUID) -> "Inventory | None":
        """Look up inventory by SKU+zone, falling back to SKU-only if no match."""
        inv = await self._repo.get_by_sku_zone(sku, zone_id)
        if inv is None:
            # Fallback: search without zone_id (handles zone mismatch scenarios)
            inv = await self._repo.get_by_sku(sku)
        return inv

    async def allocate_stock(
        self, sku: str, zone_id: uuid.UUID, qty: int
    ) -> bool:
        """Decrement available quantity.  Returns True on success."""
        inv = await self._find_inventory(sku, zone_id)
        if inv is None:
            return False

        available = inv.total_qty - inv.allocated_qty
        if available < qty:
            return False

        inv.allocated_qty += qty
        await self._repo.update(inv)
        return True

    async def release_stock(
        self, sku: str, zone_id: uuid.UUID, qty: int
    ) -> None:
        """Restore allocated quantity (e.g. on cancellation)."""
        inv = await self._find_inventory(sku, zone_id)
        if inv is None:
            return

        inv.allocated_qty = max(0, inv.allocated_qty - qty)
        await self._repo.update(inv)

    async def consume_stock(
        self, sku: str, zone_id: uuid.UUID, qty: int
    ) -> None:
        """Finalize picked stock: decrement both total_qty and allocated_qty."""
        inv = await self._find_inventory(sku, zone_id)
        if inv is None:
            return

        inv.total_qty = max(0, inv.total_qty - qty)
        inv.allocated_qty = max(0, inv.allocated_qty - qty)
        await self._repo.update(inv)
