"""Shared simulation state accessible from event handlers and routers."""

from __future__ import annotations

import uuid

from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import CellType

grid: list[list[CellType]] | None = None
traffic: TrafficController = TrafficController()
auto_dispatch: bool = False

# WES-driven simulation mode
wes_driven: bool = False
order_rate: float = 6.0  # orders per minute
station_processing_ticks: int = 5
zone_id: uuid.UUID | None = None
interactive_mode: bool = False  # True: user creates orders + scans manually

# Robot movement speeds (seconds per cell).  Higher = slower.
robot_speed: dict[str, float] = {
    "K50H": 0.4,    # 400ms per cell (heavy cargo shuttle)
    "A42TD": 0.25,  # 250ms per cell (fast rack mover)
}

# Rack-edge row: the row within the rack area that serves as the handoff
# point (formerly "cantilever row") between A42TD and K50H robots.
rack_edge_row: int | None = None


def reset() -> None:
    """Reset simulation state (for test isolation)."""
    global grid, traffic, auto_dispatch, wes_driven, order_rate
    global station_processing_ticks, zone_id, interactive_mode, rack_edge_row
    grid = None
    traffic = TrafficController()
    auto_dispatch = False
    wes_driven = False
    order_rate = 6.0
    station_processing_ticks = 5
    zone_id = None
    interactive_mode = False
    rack_edge_row = None
