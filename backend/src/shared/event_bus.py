import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class Subscription:
    event_type: type
    handler: Handler


class EventBus:
    """In-process async domain event bus for WES <-> ESS communication."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, event_type: type, handler: Handler) -> Subscription:
        self._handlers[event_type].append(handler)
        return Subscription(event_type=event_type, handler=handler)

    def unsubscribe(self, subscription: Subscription) -> None:
        handlers = self._handlers.get(subscription.event_type, [])
        if subscription.handler in handlers:
            handlers.remove(subscription.handler)

    async def publish(self, event: Any) -> None:
        """Publish an event. Handlers are invoked asynchronously."""
        await self._queue.put(event)

    def publish_nowait(self, event: Any) -> None:
        """Non-blocking publish for use in sync contexts."""
        self._queue.put_nowait(event)

    async def _process_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            event_type = type(event)
            handlers = self._handlers.get(event_type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Event handler error for %s", event_type.__name__)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def drain(self) -> None:
        """Process all pending events (useful for testing)."""
        while not self._queue.empty():
            event = self._queue.get_nowait()
            event_type = type(event)
            handlers = self._handlers.get(event_type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Event handler error for %s", event_type.__name__)


# Singleton
event_bus = EventBus()
