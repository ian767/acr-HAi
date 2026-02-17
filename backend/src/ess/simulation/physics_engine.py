"""Tick-based simulation engine driven by asyncio."""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Coroutine


class PhysicsEngine:
    """Discrete-time simulation engine that drives registered callbacks.

    Each *tick* represents one simulation step.  The engine runs an
    ``asyncio`` loop that fires ticks at a configurable interval, scaled
    by a speed multiplier (0.5x -- 10x).
    """

    MIN_SPEED: float = 0.5
    MAX_SPEED: float = 10.0

    def __init__(
        self,
        tick_interval_ms: int = 150,
        speed: float = 1.0,
    ) -> None:
        self._base_interval_ms = tick_interval_ms
        self._speed = max(self.MIN_SPEED, min(self.MAX_SPEED, speed))
        self._updatables: list[Callable[[float], Coroutine]] = []
        self._running = False
        self._paused = False
        self._task: asyncio.Task | None = None
        self._elapsed_ticks: int = 0
        self._step_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the tick loop in a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._paused = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Gracefully stop the tick loop."""
        self._running = False
        self._paused = False
        self._step_event.set()  # unblock any pending step
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def pause(self) -> None:
        """Pause the engine (ticks stop firing until resumed)."""
        self._paused = True

    def resume(self) -> None:
        """Resume after a pause."""
        self._paused = False

    async def step(self) -> None:
        """Execute exactly one tick while the engine is paused.

        If the engine is running (not paused) this is a no-op.
        """
        if not self._paused:
            return
        await self.tick()

    # ------------------------------------------------------------------
    # Speed
    # ------------------------------------------------------------------

    def set_speed(self, speed: float) -> None:
        """Set the simulation speed multiplier (clamped to 0.5x -- 10x)."""
        self._speed = max(self.MIN_SPEED, min(self.MAX_SPEED, speed))

    @property
    def speed(self) -> float:
        return self._speed

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def register_updatable(
        self,
        callback: Callable[[float], Coroutine],
    ) -> None:
        """Register an async callback ``(dt: float) -> None`` called each tick."""
        self._updatables.append(callback)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Execute one simulation tick, invoking all registered callbacks.

        *dt* passed to callbacks is the effective interval in seconds
        (base interval / speed).
        """
        dt = (self._base_interval_ms / 1000.0) / self._speed
        for callback in self._updatables:
            await callback(dt)
        self._elapsed_ticks += 1

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def elapsed_ticks(self) -> int:
        return self._elapsed_ticks

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main tick loop executed as a background task."""
        while self._running:
            if self._paused:
                await asyncio.sleep(0.05)
                continue
            interval_s = (self._base_interval_ms / 1000.0) / self._speed
            await self.tick()
            await asyncio.sleep(interval_s)
