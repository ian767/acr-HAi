import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.ess.domain.enums import (
    EquipmentTaskState,
    EquipmentTaskType,
    RobotStatus,
    RobotType,
    ToteStatus,
)


class Robot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "robots"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    type: Mapped[RobotType] = mapped_column(Enum(RobotType), nullable=False)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=False
    )
    status: Mapped[RobotStatus] = mapped_column(
        Enum(RobotStatus), default=RobotStatus.IDLE, nullable=False
    )
    grid_row: Mapped[int] = mapped_column(Integer, default=0)
    grid_col: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[float] = mapped_column(Float, default=0.0)
    current_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    speed: Mapped[float] = mapped_column(Float, default=1.0)

    # Reservation fields
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    reservation_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reservation_pick_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reservation_station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Tote Possession (K50H)
    hold_pick_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    hold_at_station: Mapped[bool] = mapped_column(Boolean, default=False)

    # A42TD territory: rectangular grid area the robot is restricted to.
    territory_col_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    territory_col_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    territory_row_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    territory_row_max: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Zone(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "zones"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    grid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_cols: Mapped[int] = mapped_column(Integer, nullable=False)

    robots: Mapped[list["Robot"]] = relationship(
        foreign_keys=[Robot.zone_id], viewonly=True
    )


class Location(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "locations"

    label: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=False
    )
    rack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    floor: Mapped[int] = mapped_column(Integer, default=1)
    grid_row: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_col: Mapped[int] = mapped_column(Integer, nullable=False)
    tote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("totes.id"), nullable=True
    )


class Tote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "totes"

    barcode: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    current_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    home_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[ToteStatus] = mapped_column(
        Enum(ToteStatus), default=ToteStatus.STORED, nullable=False
    )


class EquipmentTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "equipment_tasks"

    pick_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pick_tasks.id"), nullable=False
    )
    type: Mapped[EquipmentTaskType] = mapped_column(
        Enum(EquipmentTaskType), nullable=False
    )
    source_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id"), nullable=True
    )
    target_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id"), nullable=True
    )
    a42td_robot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("robots.id"), nullable=True
    )
    k50h_robot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("robots.id"), nullable=True
    )
    state: Mapped[EquipmentTaskState] = mapped_column(
        Enum(EquipmentTaskState), default=EquipmentTaskState.PENDING, nullable=False
    )
