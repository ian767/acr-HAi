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
    "K50H": 0.3,    # 300ms per cell (fast station shuttle)
    "A42TD": 0.5,   # 500ms per cell (slow rack mover, 1 per aisle compensates)
}

# Rack-edge row: the row within the rack area that serves as the handoff
# point (formerly "cantilever row") between A42TD and K50H robots.
rack_edge_row: int | None = None

# Aisle rows: FLOOR rows adjacent to RACK rows (narrow corridors).
# Idle A42TDs should avoid parking here to prevent blocking.
aisle_rows: set[int] = set()

# Idle points: designated parking cells for IDLE K50H robots.
idle_points: list[tuple[int, int]] = []

# Order counter for sequential external IDs (wob_sh_bx0001, 0002, ...)
order_counter: int = 0

# Live robot positions from the simulator (in-memory source of truth).
# Keyed by str(robot_id) → {"row": int, "col": int, "heading": int, "status": str}.
# Updated every tick by RobotSimulator; used by snapshot_builder to avoid
# reading stale DB defaults (grid_row=0, grid_col=0).
robot_positions: dict[str, dict] = {}

# Queue area cells: set of (row, col) for all station/approach/queue
# cells.  Updated by RobotSimulator._advance_all_queues every 5 ticks.
# Used by plan_and_store_path to avoid routing through queue zones.
queue_area_cells: set[tuple[int, int]] = set()

# Pending queue admission: station_id_str → [robot_id_str, ...]
# Robots waiting to enter station queue (Qn full). Managed by
# arrival handlers (add) and robot_simulator._pull_advance_queues (admit).
queue_pending: dict[str, list[str]] = {}


def reset() -> None:
    """Reset simulation state (for test isolation)."""
    global grid, traffic, auto_dispatch, wes_driven, order_rate
    global station_processing_ticks, zone_id, interactive_mode, rack_edge_row
    global aisle_rows, idle_points, order_counter, robot_positions, queue_area_cells
    global queue_pending
    grid = None
    traffic = TrafficController()
    auto_dispatch = False
    wes_driven = False
    order_rate = 6.0
    station_processing_ticks = 5
    zone_id = None
    interactive_mode = False
    rack_edge_row = None
    aisle_rows = set()
    idle_points = []
    order_counter = 0
    robot_positions = {}
    queue_area_cells = set()
    queue_pending = {}

    # Clear in-memory indices / trackers
    from src.wes.application.station_queue_service import clear_queue_index, reset_dissolve_debounce
    clear_queue_index()
    reset_dissolve_debounce()
    from src.ess.application.tote_origin_tracker import reset_tracker
    reset_tracker()
