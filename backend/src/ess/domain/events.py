"""Domain events emitted by ESS aggregates.

All events are plain dataclasses so they carry no infrastructure
dependencies and are trivially serialisable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Robot events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobotStatusChanged:
    robot_id: uuid.UUID
    old_status: str
    new_status: str
    position: tuple[int, int]
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class RobotPositionUpdated:
    robot_id: uuid.UUID
    row: int
    col: int
    heading: float
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Tote / Equipment-coordination events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceAtCantilever:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class SourceAtStation:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    station_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ReturnAtCantilever:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class SourceBackInRack:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    location_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class EquipmentTaskCompleted:
    task_id: uuid.UUID
    pick_task_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)
