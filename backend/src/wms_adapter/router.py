import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.deps import SessionDep
from src.wes.application.order_service import OrderService
from src.wes.domain.enums import OrderStatus
from src.wes.domain.models import Order, PickTask
from src.wms_adapter.schemas import (
    WMSOrderCancel,
    WMSOrderCreate,
    WMSOrderStatusResponse,
)

router = APIRouter()


@router.post("/orders")
async def receive_order(payload: WMSOrderCreate, session: SessionDep):
    """WMS -> WES: receive a new order."""
    svc = OrderService(session)
    order = await svc.create_order(
        external_id=payload.external_id,
        sku=payload.sku,
        quantity=payload.quantity,
        priority=payload.priority,
        zone_id=payload.zone_id,
        pbt_at=payload.pbt_at,
    )
    await session.commit()

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    return {"order_id": str(order.id), "status": order.status.value}


@router.post("/orders/cancel")
async def cancel_order(payload: WMSOrderCancel, session: SessionDep):
    """WMS -> WES: cancel an order by external_id."""
    result = await session.execute(
        select(Order).where(Order.external_id == payload.external_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    svc = OrderService(session)
    order = await svc.cancel_order(order.id)
    await session.commit()

    from src.shared.event_bus import event_bus
    for evt in svc.collect_events():
        await event_bus.publish(evt)

    return {"order_id": str(order.id), "status": order.status.value}


@router.get("/order-status/{external_id}", response_model=WMSOrderStatusResponse)
async def get_order_status(external_id: str, session: SessionDep):
    """WMS -> WES: query order status."""
    result = await session.execute(
        select(Order).where(Order.external_id == external_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Calculate pick progress
    progress = 0.0
    if order.status in (OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED):
        result = await session.execute(
            select(PickTask).where(PickTask.order_id == order.id)
        )
        tasks = result.scalars().all()
        if tasks:
            total = sum(t.qty_to_pick for t in tasks)
            picked = sum(t.qty_picked for t in tasks)
            progress = picked / total if total > 0 else 0.0

    return WMSOrderStatusResponse(
        external_id=order.external_id,
        status=order.status.value,
        pick_progress=progress,
        updated_at=order.updated_at,
    )
