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
    target_tote_barcode: str | None = None
    put_wall_slot_id: uuid.UUID | None = None
    state: str

    model_config = {"from_attributes": True}


class CompleteBody(BaseModel):
    pick_task_id: uuid.UUID


class ScanBody(BaseModel):
    pick_task_id: uuid.UUID


class BindToteBody(BaseModel):
    pick_task_id: uuid.UUID
    target_tote_id: uuid.UUID | None = None
    target_tote_barcode: str | None = None


class ToteFullBody(BaseModel):
    pick_task_id: uuid.UUID


class InventoryOut(BaseModel):
    id: uuid.UUID
    sku: str
    sku_name: str | None = None
    band: str = "C"
    zone_id: uuid.UUID | None = None
    total_qty: int
    allocated_qty: int

    model_config = {"from_attributes": True}


class CreateOrderBody(BaseModel):
    sku: str
    quantity: int = Field(ge=1)
    priority: int = 0
    external_id: str | None = None


class ReleaseModeBody(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(body: CreateOrderBody, session: SessionDep):
    from sqlalchemy import select, func
    from src.ess.domain.models import Tote
    from src.shared import simulation_state

    # Verify at least one tote exists with physical location and stock for this SKU
    count = await session.execute(
        select(func.count()).select_from(Tote).where(
            Tote.sku == body.sku,
            Tote.quantity > 0,
            Tote.current_location_id.isnot(None),
        )
    )
    if count.scalar_one() == 0:
        raise HTTPException(
            status_code=422,
            detail=f"No fulfillable tote for SKU {body.sku}. "
            "Apply a simulation preset so totes have physical map locations.",
        )

    svc = OrderService(session)
    import src.shared.simulation_state as sim_state
    sim_state.order_counter += 1
    ext_id = body.external_id or f"wob_sh_bx{sim_state.order_counter:04d}"
    order = await svc.create_order(
        external_id=ext_id,
        sku=body.sku,
        quantity=body.quantity,
        priority=body.priority,
        zone_id=simulation_state.zone_id,
    )

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    # Auto-allocate in interactive mode so robots get dispatched immediately.
    if simulation_state.interactive_mode and simulation_state.zone_id is not None:
        try:
            order = await svc.allocate_order(order.id)
            for evt in svc.collect_events():
                await event_bus.publish(evt)
        except (ValueError, RuntimeError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Auto-allocate failed for order %s: %s", order.id, exc
            )

    return order


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
    from src.wes.domain.models import Station
    from src.ess.domain.models import Robot
    from sqlalchemy import select

    svc = PickTaskService(session)
    try:
        # Verify the pick task belongs to this station
        task_check = await svc.get_pick_task(body.pick_task_id)
        if task_check.station_id != station_id:
            raise HTTPException(
                status_code=400,
                detail="Pick task does not belong to this station",
            )

        # CV-1: Verify a robot holding this task's tote is at the station
        station = await session.get(Station, station_id)
        robot_present = False
        if station is not None:
            # Method 1: Check station.current_robot_id
            if station.current_robot_id is not None:
                robot = await session.get(Robot, station.current_robot_id)
                if robot is not None and robot.hold_pick_task_id == body.pick_task_id:
                    robot_present = True

            # Method 2: Search for any robot reserved at this station with hold_at_station
            if not robot_present:
                result = await session.execute(
                    select(Robot).where(
                        Robot.hold_at_station == True,  # noqa: E712
                        Robot.hold_pick_task_id == body.pick_task_id,
                        Robot.reservation_station_id == station_id,
                    ).limit(1)
                )
                if result.scalar_one_or_none() is not None:
                    robot_present = True

        if not robot_present:
            raise HTTPException(
                status_code=400,
                detail="Cannot scan: no robot holding this task's tote is at the station",
            )

        task = await svc.scan_item(body.pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    # When all items picked (auto-transition to RETURN_REQUESTED), trigger
    # the return flow so the source tote goes back to the rack.
    if task.state == PickTaskState.RETURN_REQUESTED and task.source_tote_id:
        from src.ess.domain.models import Tote
        source_tote = await session.get(Tote, task.source_tote_id)
        if source_tote is not None and source_tote.home_location_id is not None:
            from src.wes.domain.events import ReturnSourceTote
            await event_bus.publish(ReturnSourceTote(
                pick_task_id=task.id,
                tote_id=task.source_tote_id,
                target_location_id=source_tote.home_location_id,
                station_id=station_id,
            ))

    return task


@router.post("/stations/{station_id}/complete")
async def complete_at_station(
    station_id: uuid.UUID,
    body: CompleteBody,
    session: SessionDep,
):
    """Complete a pick task at station with CV-1 robot validation.

    Validates:
    1. A robot is present at the station (current_robot_id or fallback search)
    2. Robot's reservation matches the station
    3. Robot holds a tote at the station
    4. Robot's held pick_task matches the request
    """
    from src.wes.application.reservation_service import ReservationService
    from src.wes.application.station_queue_service import StationQueueService
    from src.wes.application.order_service import OrderService
    from src.wes.domain.enums import PickTaskState
    from src.wes.domain.models import PickTask, Station
    from src.ess.domain.models import Robot
    from sqlalchemy import select

    # Get station
    station = await session.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    # CV-1 validation: find robot at station
    robot = None
    if station.current_robot_id:
        robot = await session.get(Robot, station.current_robot_id)

    if robot is None:
        # Fallback: search for reserved robot at station
        rsvc = ReservationService(session)
        robot = await rsvc.find_reserved_robot_at_station(station_id)

    if robot is None:
        raise HTTPException(
            status_code=400,
            detail="No robot at station",
        )

    # Validate reservation matches
    if robot.reservation_station_id != station_id:
        raise HTTPException(
            status_code=400,
            detail="Robot not reserved for this station",
        )

    if not robot.hold_at_station:
        raise HTTPException(
            status_code=400,
            detail="Robot not holding tote at station",
        )

    if robot.hold_pick_task_id != body.pick_task_id:
        raise HTTPException(
            status_code=400,
            detail="Robot holding different pick task",
        )

    # All CV-1 checks passed - complete the pick task
    pts = PickTaskService(session)
    try:
        task = await pts.complete_at_station(body.pick_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Clear tote possession and release reservation
    rsvc = ReservationService(session)
    await rsvc.clear_reservation(robot.id)

    # Release station slot and advance queue
    qsvc = StationQueueService(session)
    await qsvc.release_station(station_id, robot.id)

    # Check if order is complete
    pick_task = await pts.get_pick_task(body.pick_task_id)
    result = await session.execute(
        select(PickTask).where(
            PickTask.order_id == pick_task.order_id,
            PickTask.state != PickTaskState.COMPLETED,
        )
    )
    incomplete = result.scalars().all()
    if not incomplete:
        os = OrderService(session)
        await os.complete_order(pick_task.order_id)
        from src.shared.event_bus import event_bus
        for evt in os.collect_events():
            await event_bus.publish(evt)

    from src.shared.event_bus import event_bus
    for evt in pts.collect_events():
        await event_bus.publish(evt)

    await session.commit()

    from src.handler_support import ws_broadcast
    await ws_broadcast("pickTask.state_changed", {
        "pick_task_id": str(body.pick_task_id),
        "order_id": str(pick_task.order_id),
        "station_id": str(station_id),
        "from": "SOURCE_AT_STATION",
        "to": "COMPLETED",
    })

    return {
        "status": "completed",
        "pick_task_id": str(body.pick_task_id),
        "robot_id": str(robot.id),
    }


@router.post("/stations/{station_id}/bind-tote", response_model=PickTaskOut)
async def bind_target_tote(
    station_id: uuid.UUID,
    body: BindToteBody,
    session: SessionDep,
):
    """Bind a target (destination) tote to a pick task at this station.

    Accepts either target_tote_id (UUID) or target_tote_barcode (string).
    If barcode is provided, looks up the tote by barcode first.
    """
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

    target_id = body.target_tote_id
    if target_id is None and body.target_tote_barcode:
        # Look up tote by barcode
        from src.ess.domain.models import Tote
        from sqlalchemy import select

        result = await session.execute(
            select(Tote).where(
                Tote.barcode == body.target_tote_barcode,
            ).limit(1)
        )
        tote = result.scalar_one_or_none()
        if tote is not None:
            target_id = tote.id
        else:
            # Create a virtual tote for the barcode (simulation convenience)
            tote = Tote(
                barcode=body.target_tote_barcode,
                sku=None,
                quantity=0,
            )
            session.add(tote)
            await session.flush()
            target_id = tote.id

    if target_id is None:
        raise HTTPException(
            status_code=400,
            detail="Provide target_tote_id or target_tote_barcode",
        )

    # Prevent binding the same tote to multiple active pick tasks.
    from src.wes.domain.models import PickTask as PTModel
    from src.wes.domain.enums import PickTaskState
    from sqlalchemy import select

    existing = await session.execute(
        select(PTModel).where(
            PTModel.target_tote_id == target_id,
            PTModel.state.notin_([
                PickTaskState.COMPLETED.value,
            ]),
            PTModel.id != body.pick_task_id,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="This tote is already bound to another active pick task",
        )

    task.target_tote_id = target_id
    # Resolve barcode for display
    if body.target_tote_barcode:
        task.target_tote_barcode = body.target_tote_barcode
    else:
        from src.ess.domain.models import Tote as ToteModel
        tote_obj = await session.get(ToteModel, target_id)
        task.target_tote_barcode = tote_obj.barcode if tote_obj else None

    # Link to matching putwall slot (critical for slot display + tote-full)
    if task.put_wall_slot_id is None:
        from src.wes.domain.models import PutWallSlot
        slot_result = await session.execute(
            select(PutWallSlot).where(
                PutWallSlot.station_id == station_id,
                PutWallSlot.target_tote_id == target_id,
                PutWallSlot.is_locked == False,  # noqa: E712
            ).limit(1)
        )
        matching_slot = slot_result.scalar_one_or_none()
        if matching_slot is not None:
            task.put_wall_slot_id = matching_slot.id
            matching_slot.is_locked = True

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

    # Clear target tote so operator can bind a new one
    task.target_tote_id = None

    # Clear the put-wall slot (unbind tote, unlock)
    if task.put_wall_slot_id:
        from src.wes.domain.models import PutWallSlot
        slot = await session.get(PutWallSlot, task.put_wall_slot_id)
        if slot is not None:
            slot.target_tote_id = None
            slot.target_tote_barcode = None
            slot.is_locked = False
        task.put_wall_slot_id = None

    # Determine if all picks are done.
    from src.wes.domain.enums import PickTaskState
    all_picked = task.qty_picked >= task.qty_to_pick and task.qty_to_pick > 0
    already_returning = task.state == PickTaskState.RETURN_REQUESTED

    # Only trigger return flow if all picks are done (or already in return state).
    # If tote is just full but picks remain, operator needs a new target tote.
    if (all_picked or already_returning) and task.source_tote_id:
        # Transition to RETURN_REQUESTED if still in PICKING
        if task.state == PickTaskState.PICKING:
            from src.wes.domain.state_machines.pick_task_sm import pick_task_sm
            new_state, _side_effects = pick_task_sm.transition(task.state, "pick_complete")
            task.state = new_state

        # Only publish ReturnSourceTote if no RETURN equipment task exists yet
        from sqlalchemy import select
        from src.ess.domain.models import EquipmentTask as EqTask
        from src.ess.domain.enums import EquipmentTaskType
        existing_return = await session.execute(
            select(EqTask).where(
                EqTask.pick_task_id == task.id,
                EqTask.type == EquipmentTaskType.RETURN,
            ).limit(1)
        )
        if existing_return.scalar_one_or_none() is None:
            from src.ess.domain.models import Tote as ToteModel
            source_tote = await session.get(ToteModel, task.source_tote_id)
            if source_tote is not None and source_tote.home_location_id is not None:
                from src.wes.domain.events import ReturnSourceTote
                from src.shared.event_bus import event_bus
                await event_bus.publish(ReturnSourceTote(
                    pick_task_id=task.id,
                    tote_id=task.source_tote_id,
                    target_location_id=source_tote.home_location_id,
                    station_id=station_id,
                ))

    await session.commit()
    return task


# ---------------------------------------------------------------------------
# Put-Wall Slots
# ---------------------------------------------------------------------------


class PutWallSlotOut(BaseModel):
    id: uuid.UUID
    station_id: uuid.UUID
    slot_label: str
    target_tote_id: uuid.UUID | None = None
    target_tote_barcode: str | None = None
    is_locked: bool = False

    model_config = {"from_attributes": True}


class BindSlotBody(BaseModel):
    slot_id: uuid.UUID
    tote_barcode: str


@router.get("/stations/{station_id}/putwall", response_model=list[PutWallSlotOut])
async def get_putwall(station_id: uuid.UUID, session: SessionDep):
    """Return putwall slot state for a station."""
    from src.wes.domain.models import PutWallSlot
    from sqlalchemy import select

    result = await session.execute(
        select(PutWallSlot)
        .where(PutWallSlot.station_id == station_id)
        .order_by(PutWallSlot.slot_label)
    )
    return list(result.scalars().all())


@router.post(
    "/stations/{station_id}/putwall/bind-slot", response_model=PutWallSlotOut
)
async def bind_putwall_slot(
    station_id: uuid.UUID,
    body: BindSlotBody,
    session: SessionDep,
):
    """Bind a tote to a specific putwall slot (independent of tasks)."""
    from src.wes.domain.models import PutWallSlot
    from src.ess.domain.models import Tote
    from sqlalchemy import select

    slot = await session.get(PutWallSlot, body.slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.station_id != station_id:
        raise HTTPException(
            status_code=400, detail="Slot does not belong to this station"
        )

    # Look up or create tote by barcode
    result = await session.execute(
        select(Tote).where(Tote.barcode == body.tote_barcode).limit(1)
    )
    tote = result.scalar_one_or_none()
    if tote is None:
        tote = Tote(barcode=body.tote_barcode, sku=None, quantity=0)
        session.add(tote)
        await session.flush()

    # Prevent same tote in multiple active slots at this station
    existing = await session.execute(
        select(PutWallSlot).where(
            PutWallSlot.station_id == station_id,
            PutWallSlot.target_tote_id == tote.id,
            PutWallSlot.id != slot.id,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="This tote is already bound to another slot at this station",
        )

    slot.target_tote_id = tote.id
    slot.target_tote_barcode = body.tote_barcode
    await session.commit()
    return slot


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


@router.get("/inventory/skus")
async def list_available_skus(session: SessionDep):
    """Return distinct SKUs with available stock."""
    repo = InventoryRepository(session)
    items = await repo.list(limit=500)
    skus = [
        {"sku": inv.sku, "available_qty": inv.total_qty - inv.allocated_qty}
        for inv in items
        if inv.total_qty - inv.allocated_qty > 0
    ]
    return skus


# ---------------------------------------------------------------------------
# Totes (LPN-level detail)
# ---------------------------------------------------------------------------


class ToteDetailOut(BaseModel):
    id: uuid.UUID
    barcode: str
    sku: str | None = None
    sku_name: str | None = None
    band: str | None = None
    quantity: int
    status: str
    location_label: str | None = None

    model_config = {"from_attributes": True}


@router.get("/totes", response_model=list[ToteDetailOut])
async def list_totes(
    session: SessionDep,
    sku: str | None = Query(None),
    barcode: str | None = Query(None),
    band: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List totes with inventory and location detail (LEFT JOIN)."""
    from sqlalchemy import select
    from src.ess.domain.models import Location, Tote
    from src.wes.domain.models import Inventory

    stmt = (
        select(
            Tote.id,
            Tote.barcode,
            Tote.sku,
            Tote.quantity,
            Tote.status,
            Location.label.label("location_label"),
            Inventory.sku_name,
            Inventory.band,
        )
        .outerjoin(Location, Tote.current_location_id == Location.id)
        .outerjoin(Inventory, Tote.sku == Inventory.sku)
    )

    if sku:
        stmt = stmt.where(Tote.sku == sku)
    if barcode:
        stmt = stmt.where(Tote.barcode.ilike(f"%{barcode}%"))
    if band:
        stmt = stmt.where(Inventory.band == band)
    if status:
        stmt = stmt.where(Tote.status == status)

    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.all()

    return [
        ToteDetailOut(
            id=row.id,
            barcode=row.barcode,
            sku=row.sku,
            sku_name=row.sku_name,
            band=row.band,
            quantity=row.quantity,
            status=row.status.value if hasattr(row.status, "value") else str(row.status),
            location_label=row.location_label,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Inventory Seed
# ---------------------------------------------------------------------------

SKU_NAMES = [
    "Widget Alpha", "Gizmo Beta", "Sprocket Gamma", "Flange Delta",
    "Coupler Epsilon", "Bracket Zeta", "Gasket Eta", "Bearing Theta",
    "Piston Iota", "Valve Kappa", "Nozzle Lambda", "Sleeve Mu",
    "Collar Nu", "Bushing Xi", "Anchor Omicron", "Rivet Pi",
    "Washer Rho", "Bolt Sigma", "Nut Tau", "Pin Upsilon",
    "Cam Phi", "Gear Chi", "Shaft Psi", "Hub Omega",
    "Drum Alpha-2", "Lever Beta-2", "Hinge Gamma-2", "Axle Delta-2",
    "Spring Epsilon-2", "Clamp Zeta-2",
]

BANDS = ["A", "B", "C", "D", "E", "F"]

SEED_PRESETS = {
    "small":  {"sku_count": 5,  "totes_per_sku": 2, "qty_per_tote": 10},
    "medium": {"sku_count": 15, "totes_per_sku": 4, "qty_per_tote": 10},
    "large":  {"sku_count": 30, "totes_per_sku": 6, "qty_per_tote": 15},
}


class SeedRequest(BaseModel):
    preset: str = "medium"


@router.post("/inventory/seed")
async def seed_inventory(body: SeedRequest, session: SessionDep):
    """Seed standalone inventory (no zone required)."""
    if body.preset not in SEED_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {body.preset}")

    cfg = SEED_PRESETS[body.preset]
    sku_count = cfg["sku_count"]
    totes_per_sku = cfg["totes_per_sku"]
    qty_per_tote = cfg["qty_per_tote"]

    from sqlalchemy import delete
    from src.ess.domain.models import Tote
    from src.wes.domain.models import Inventory

    # Clear existing totes and inventory.
    await session.execute(delete(Inventory))
    await session.execute(delete(Tote))
    await session.flush()

    totes_created = 0
    for sku_num in range(1, sku_count + 1):
        sku = f"SKU-{sku_num:03d}"
        sku_name = SKU_NAMES[(sku_num - 1) % len(SKU_NAMES)]
        band = BANDS[(sku_num - 1) % len(BANDS)]

        for t in range(totes_per_sku):
            tote = Tote(
                barcode=f"SEED-{sku_num:03d}-{t+1:02d}",
                sku=sku,
                quantity=qty_per_tote,
            )
            session.add(tote)
            totes_created += 1

        inv = Inventory(
            sku=sku,
            sku_name=sku_name,
            band=band,
            zone_id=None,
            total_qty=qty_per_tote * totes_per_sku,
            allocated_qty=0,
        )
        session.add(inv)

    await session.commit()

    return {
        "status": "seeded",
        "preset": body.preset,
        "sku_count": sku_count,
        "totes_created": totes_created,
    }


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
