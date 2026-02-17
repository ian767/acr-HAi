import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


@dataclass
class WSMessage:
    type: str
    payload: dict[str, Any]
    timestamp: float


class WebSocketManager:
    """Manages WebSocket connections and broadcasts real-time updates to frontend."""

    def __init__(self, throttle_ms: int = 100, backpressure_limit: int = 65536) -> None:
        self._connections: list[WebSocket] = []
        self._throttle_ms = throttle_ms
        self._backpressure_limit = backpressure_limit
        self._last_robot_broadcast: float = 0.0
        self._pending_robot_updates: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket connected. Total: %d", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message_type: str, payload: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        if not self._connections:
            return

        msg = WSMessage(
            type=message_type,
            payload=payload,
            timestamp=time.time(),
        )
        data = json.dumps(asdict(msg))

        disconnected: list[WebSocket] = []
        for ws in self._connections:
            try:
                # Backpressure check for robot updates
                if message_type == "robot.updated":
                    buffered = getattr(ws, "_send_buffer_size", 0)
                    if buffered > self._backpressure_limit:
                        continue
                await ws.send_text(data)
            except WebSocketDisconnect:
                disconnected.append(ws)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_robot_updates(self, updates: dict[str, dict]) -> None:
        """Throttled broadcast for high-frequency robot position updates."""
        now = time.time() * 1000
        async with self._lock:
            self._pending_robot_updates.update(updates)

            if now - self._last_robot_broadcast < self._throttle_ms:
                return

            if not self._pending_robot_updates:
                return

            await self.broadcast("robot.updated", {
                "robots": self._pending_robot_updates,
            })
            self._pending_robot_updates = {}
            self._last_robot_broadcast = now

    async def send_snapshot(self, websocket: WebSocket, snapshot: dict[str, Any]) -> None:
        """Send full state snapshot on initial connection."""
        msg = WSMessage(
            type="snapshot",
            payload=snapshot,
            timestamp=time.time(),
        )
        await websocket.send_text(json.dumps(asdict(msg)))

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Singleton
ws_manager = WebSocketManager()
