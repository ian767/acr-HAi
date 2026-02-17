import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# === Inbound: WMS -> WES ===

class WMSOrderCreate(BaseModel):
    external_id: str = Field(..., max_length=100)
    sku: str = Field(..., max_length=50)
    quantity: int = Field(..., gt=0)
    priority: int = Field(default=0, ge=0, le=10)
    zone_id: uuid.UUID | None = None
    pbt_at: datetime | None = None


class WMSOrderCancel(BaseModel):
    external_id: str = Field(..., max_length=100)
    reason: str | None = None


class WMSOrderStatusResponse(BaseModel):
    external_id: str
    status: str
    station_name: str | None = None
    pick_progress: float = 0.0  # 0.0 ~ 1.0
    updated_at: datetime | None = None


# === Outbound: WES -> WMS ===

class WMSOrderCompletedReport(BaseModel):
    external_id: str
    order_id: uuid.UUID
    completed_at: datetime
    items_picked: int


class WMSInventoryUpdateReport(BaseModel):
    sku: str
    zone_id: uuid.UUID
    qty_change: int
    reason: str  # "picked", "cancelled", "adjustment"
