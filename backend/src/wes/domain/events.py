"""Domain events emitted by WES aggregates.

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
# Order events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderCreated:
    order_id: uuid.UUID
    external_id: str
    sku: str
    quantity: int
    priority: int
    zone_id: uuid.UUID | None
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class OrderAllocated:
    order_id: uuid.UUID
    station_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class OrderCompleted:
    order_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class OrderCancelled:
    order_id: uuid.UUID
    reason: str = ""
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# PickTask events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PickTaskCreated:
    pick_task_id: uuid.UUID
    order_id: uuid.UUID
    station_id: uuid.UUID
    sku: str
    qty_to_pick: int
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class PickTaskStateChanged:
    pick_task_id: uuid.UUID
    previous_state: str
    new_state: str
    event: str
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Equipment-coordination events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrieveSourceTote:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    source_location_id: uuid.UUID
    station_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ReturnSourceTote:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    target_location_id: uuid.UUID
    station_id: uuid.UUID
    event_id: uuid.UUID = field(default_factory=_new_id)
    occurred_at: datetime = field(default_factory=_utcnow)
