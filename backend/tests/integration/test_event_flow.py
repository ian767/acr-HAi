"""Integration test: WES <-> ESS event flow via event bus."""

import asyncio
import uuid
from dataclasses import dataclass

import pytest

from src.shared.event_bus import EventBus


@dataclass
class MockRetrieveSourceTote:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    source_location_id: uuid.UUID
    station_id: uuid.UUID


@dataclass
class MockSourceAtCantilever:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID


@dataclass
class MockSourceAtStation:
    pick_task_id: uuid.UUID
    tote_id: uuid.UUID
    station_id: uuid.UUID


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.mark.asyncio
async def test_publish_subscribe(event_bus: EventBus):
    received = []

    async def handler(event: MockSourceAtCantilever):
        received.append(event)

    event_bus.subscribe(MockSourceAtCantilever, handler)

    pick_id = uuid.uuid4()
    tote_id = uuid.uuid4()
    event = MockSourceAtCantilever(pick_task_id=pick_id, tote_id=tote_id)

    await event_bus.publish(event)
    await event_bus.drain()

    assert len(received) == 1
    assert received[0].pick_task_id == pick_id


@pytest.mark.asyncio
async def test_multiple_subscribers(event_bus: EventBus):
    results_a = []
    results_b = []

    async def handler_a(event):
        results_a.append(event)

    async def handler_b(event):
        results_b.append(event)

    event_bus.subscribe(MockSourceAtCantilever, handler_a)
    event_bus.subscribe(MockSourceAtCantilever, handler_b)

    event = MockSourceAtCantilever(pick_task_id=uuid.uuid4(), tote_id=uuid.uuid4())
    await event_bus.publish(event)
    await event_bus.drain()

    assert len(results_a) == 1
    assert len(results_b) == 1


@pytest.mark.asyncio
async def test_different_event_types_isolated(event_bus: EventBus):
    cantilever_events = []
    station_events = []

    async def on_cantilever(event):
        cantilever_events.append(event)

    async def on_station(event):
        station_events.append(event)

    event_bus.subscribe(MockSourceAtCantilever, on_cantilever)
    event_bus.subscribe(MockSourceAtStation, on_station)

    await event_bus.publish(
        MockSourceAtCantilever(pick_task_id=uuid.uuid4(), tote_id=uuid.uuid4())
    )
    await event_bus.drain()

    assert len(cantilever_events) == 1
    assert len(station_events) == 0


@pytest.mark.asyncio
async def test_unsubscribe(event_bus: EventBus):
    received = []

    async def handler(event):
        received.append(event)

    sub = event_bus.subscribe(MockSourceAtCantilever, handler)
    event_bus.unsubscribe(sub)

    await event_bus.publish(
        MockSourceAtCantilever(pick_task_id=uuid.uuid4(), tote_id=uuid.uuid4())
    )
    await event_bus.drain()

    assert len(received) == 0


@pytest.mark.asyncio
async def test_wes_ess_round_trip(event_bus: EventBus):
    """Simulate WES -> ESS -> WES event chain."""
    pick_task_id = uuid.uuid4()
    tote_id = uuid.uuid4()
    station_id = uuid.uuid4()
    location_id = uuid.uuid4()

    ess_received = []
    wes_received = []

    # ESS subscribes to WES retrieve command
    async def ess_on_retrieve(event: MockRetrieveSourceTote):
        ess_received.append(event)
        # ESS completes step 1 -> publishes back to WES
        await event_bus.publish(
            MockSourceAtCantilever(pick_task_id=event.pick_task_id, tote_id=event.tote_id)
        )

    # WES subscribes to ESS cantilever event
    async def wes_on_cantilever(event: MockSourceAtCantilever):
        wes_received.append(event)

    event_bus.subscribe(MockRetrieveSourceTote, ess_on_retrieve)
    event_bus.subscribe(MockSourceAtCantilever, wes_on_cantilever)

    # WES publishes retrieve command
    await event_bus.publish(
        MockRetrieveSourceTote(
            pick_task_id=pick_task_id,
            tote_id=tote_id,
            source_location_id=location_id,
            station_id=station_id,
        )
    )
    # Drain twice: first for retrieve handler, then for cantilever handler
    await event_bus.drain()
    await event_bus.drain()

    assert len(ess_received) == 1
    assert len(wes_received) == 1
    assert wes_received[0].pick_task_id == pick_task_id


@pytest.mark.asyncio
async def test_handler_error_does_not_break_others(event_bus: EventBus):
    results = []

    async def failing_handler(event):
        raise RuntimeError("oops")

    async def good_handler(event):
        results.append(event)

    event_bus.subscribe(MockSourceAtCantilever, failing_handler)
    event_bus.subscribe(MockSourceAtCantilever, good_handler)

    await event_bus.publish(
        MockSourceAtCantilever(pick_task_id=uuid.uuid4(), tote_id=uuid.uuid4())
    )
    await event_bus.drain()

    assert len(results) == 1
