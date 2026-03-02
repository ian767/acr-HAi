import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.wes.domain.enums import OrderStatus, PickTaskState, StationStatus


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    external_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    pbt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.NEW, nullable=False
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id"), nullable=True
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=True
    )

    pick_tasks: Mapped[list["PickTask"]] = relationship(back_populates="order")


class PickTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pick_tasks"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    qty_to_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_picked: Mapped[int] = mapped_column(Integer, default=0)
    source_tote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("totes.id"), nullable=True
    )
    target_tote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    target_tote_barcode: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    put_wall_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("put_wall_slots.id"), nullable=True
    )
    state: Mapped[PickTaskState] = mapped_column(
        Enum(PickTaskState), default=PickTaskState.CREATED, nullable=False
    )
    assigned_robot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("robots.id"), nullable=True
    )

    order: Mapped["Order"] = relationship(back_populates="pick_tasks")


class Station(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stations"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=False
    )
    grid_row: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_col: Mapped[int] = mapped_column(Integer, nullable=False)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[StationStatus] = mapped_column(
        Enum(StationStatus), default=StationStatus.IDLE, nullable=False
    )
    max_queue_size: Mapped[int] = mapped_column(Integer, default=6)

    # Station queue cell positions
    approach_cell_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approach_cell_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    holding_cell_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    holding_cell_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_cells_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_robot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    queue_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    put_wall_slots: Mapped[list["PutWallSlot"]] = relationship(back_populates="station")


class PutWallSlot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "put_wall_slots"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id"), nullable=False
    )
    slot_label: Mapped[str] = mapped_column(String(10), nullable=False)
    target_tote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    target_tote_barcode: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    station: Mapped["Station"] = relationship(back_populates="put_wall_slots")


class Inventory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory"

    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    sku_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    band: Mapped[str] = mapped_column(String(1), default="C", nullable=False)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=True
    )
    total_qty: Mapped[int] = mapped_column(Integer, default=0)
    allocated_qty: Mapped[int] = mapped_column(Integer, default=0)
