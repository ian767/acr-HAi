"""FastAPI router for the Warehouse Execution System (WES) API."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.deps import SessionDep
from src.wes.application.inventory_service import InventoryService
from src.wes.application.order_service import OrderService
from src.wes.application.pick_task_service import PickTaskService
from src.wes.application.station_service import StationService
from src.wes.domain.enums import OrderStatus, PickTaskState
from src.wes.infrastructure.repositories import InventoryRepository

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------


class OrderOut(BaseModel):
    id: uuid.UUID
    external_id: str
    sku: str
    quantity: int
    priority: int
    pbt_at: datetime | None = None
    status: str
    station_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class AllocateResponse(BaseModel):
    id: uuid.UUID
    status: str
    station_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class StationOut(BaseModel):
    id: uuid.UUID
    name: str
    zone_id: uuid.UUID
    grid_row: int
    grid_col: int
    is_online: bool
    status: str
    max_queue_size: int

    model_config = {"from_attributes": True}


class SetOnlineBody(BaseModel):
    online: bool


class PickTaskOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    station_id: uuid.UUID
    sku: str
    qty_to_pick: int
    qty_picked: int
    source_tote_id: uuid.UUID | None = None
    target_tote_id: uuid.UUID | None = None
    state: str

    model_config = {"from_attributes": True}


class ScanBody(BaseModel):
    pick_task_id: uuid.UUID


class BindToteBody(BaseModel):
    pick_task_id: uuid.UUID
    target_tote_id: uuid.UUID


class ToteFullBody(BaseModel):
    pick_task_id: uuid.UUID


class InventoryOut(BaseModel):
    id: uuid.UUID
    sku: str
    zone_id: uuid.UUID
    total_qty: int
    allocated_qty: int

    model_config = {"from_attributes": True}


class ReleaseModeBody(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    session: SessionDep,
    status: OrderStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    svc = OrderService(session)
    orders = await svc.list_orders(status=status, limit=limit, offset=offset)
    return orders


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, session: SessionDep):
    svc = OrderService(session)
    try:
        order = await svc.get_order(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return order


@router.post("/orders/{order_id}/allocate", response_model=AllocateResponse)
async def allocate_order(order_id: uuid.UUID, session: SessionDep):
    svc = OrderService(session)
    try:
        order = await svc.allocate_order(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    return order


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------


@router.get("/stations", response_model=list[StationOut])
async def list_stations(
    session: SessionDep,
    zone_id: uuid.UUID | None = Query(None),
):
    svc = StationService(session)
    stations = await svc.list_stations(zone_id=zone_id)
    return stations


@router.put("/stations/{station_id}/online", response_model=StationOut)
async def toggle_station_online(
    station_id: uuid.UUID,
    body: SetOnlineBody,
    session: SessionDep,
):
    svc = StationService(session)
    try:
        station = await svc.set_online(station_id, body.online)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return station


# ---------------------------------------------------------------------------
# Pick Tasks
# ---------------------------------------------------------------------------


@router.get("/pick-tasks", response_model=list[PickTaskOut])
async def list_pick_tasks(
    session: SessionDep,
    station_id: uuid.UUID | None = Query(None),
    state: PickTaskState | None = Query(None),
):
    svc = PickTaskService(session)
    tasks = await svc.list_pick_tasks(station_id=station_id, state=state)
    return tasks


@router.get("/pick-tasks/{pick_task_id}", response_model=PickTaskOut)
async def get_pick_task(pick_task_id: uuid.UUID, session: SessionDep):
    svc = PickTaskService(session)
    try:
        task = await svc.get_pick_task(pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task


@router.post("/stations/{station_id}/scan", response_model=PickTaskOut)
async def scan_item(
    station_id: uuid.UUID,
    body: ScanBody,
    session: SessionDep,
):
    svc = PickTaskService(session)
    try:
        task = await svc.scan_item(body.pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    return task


@router.post("/stations/{station_id}/bind-tote", response_model=PickTaskOut)
async def bind_target_tote(
    station_id: uuid.UUID,
    body: BindToteBody,
    session: SessionDep,
):
    """Bind a target (destination) tote to a pick task at this station."""
    svc = PickTaskService(session)
    try:
        task = await svc.get_pick_task(body.pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if task.station_id != station_id:
        raise HTTPException(
            status_code=400,
            detail="Pick task does not belong to this station",
        )

    task.target_tote_id = body.target_tote_id
    await session.commit()
    return task


@router.post("/stations/{station_id}/tote-full", response_model=PickTaskOut)
async def handle_tote_full(
    station_id: uuid.UUID,
    body: ToteFullBody,
    session: SessionDep,
):
    """Handle a full target tote event: clear target tote so operator can swap."""
    svc = PickTaskService(session)
    try:
        task = await svc.get_pick_task(body.pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if task.station_id != station_id:
        raise HTTPException(
            status_code=400,
            detail="Pick task does not belong to this station",
        )

    # Clear target tote so operator must bind a new one
    task.target_tote_id = None
    await session.commit()
    return task


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@router.get("/inventory", response_model=list[InventoryOut])
async def list_inventory(
    session: SessionDep,
    sku: str | None = Query(None),
    zone_id: uuid.UUID | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    repo = InventoryRepository(session)
    items = await repo.list(sku=sku, zone_id=zone_id, limit=limit, offset=offset)
    return list(items)


# ---------------------------------------------------------------------------
# Release mode
# ---------------------------------------------------------------------------


@router.put("/release-mode")
async def toggle_release_mode(body: ReleaseModeBody):
    """Toggle automatic release mode for completed orders.

    This is a runtime configuration toggle.  In a production system this
    would be persisted; here we simply acknowledge the request.
    """
    return {"release_mode": body.enabled}
