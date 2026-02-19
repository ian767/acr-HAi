"""FastAPI router for the Equipment Scheduling System (ESS)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.deps import SessionDep
from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.zone_manager import ZoneManager
from src.ess.domain.enums import CellType, RobotStatus, RobotType
from src.ess.simulation.physics_engine import PhysicsEngine
from src.ess.simulation.presets import SimulationPresets
from src.ess.simulation.robot_simulator import RobotSimulator
from src.ess.infrastructure.redis_cache import RobotStateCache
from src.shared.redis import get_redis
import src.shared.simulation_state as simulation_state

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level simulation singletons (initialised lazily on first use).
# ---------------------------------------------------------------------------

_engine: PhysicsEngine | None = None
_simulator: RobotSimulator | None = None


def _get_engine() -> PhysicsEngine:
    global _engine
    if _engine is None:
        _engine = PhysicsEngine()
    return _engine


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class FlowRequest(BaseModel):
    pick_task_id: uuid.UUID
    order_id: uuid.UUID
    station_id: uuid.UUID
    from_location: uuid.UUID | None = None
    source_tote_id: uuid.UUID | None = None


class SpeedRequest(BaseModel):
    speed: float


class PresetRequest(BaseModel):
    name: str


class CustomPresetRequest(BaseModel):
    """User-configurable preset parameters."""
    zone_rows: int = 20
    zone_cols: int = 30
    a42td_count: int = 3
    k50h_count: int = 2
    rack_row_start: int = 2
    rack_row_end: int = 8
    rack_col_start: int = 2
    rack_col_end: int = 12
    rack_edge_row: int = 9
    stations: list[dict] = []  # [{"row": 18, "col": 5}, ...]
    station_count: int = 0  # auto-generate stations if > 0 and stations is empty
    wes_driven: bool = True
    interactive_mode: bool = False
    orders_per_minute: float = 6.0
    station_processing_ticks: int = 5
    totes: int = 20
    sku_count: int = 10
    speed: float = 1.0


class RobotOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    zone_id: uuid.UUID
    status: str
    grid_row: int
    grid_col: int
    heading: float
    current_task_id: uuid.UUID | None

    class Config:
        from_attributes = True


class ZoneOut(BaseModel):
    id: uuid.UUID
    name: str
    grid_rows: int
    grid_cols: int

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Robot endpoints
# ---------------------------------------------------------------------------


@router.get("/robots", response_model=list[RobotOut])
async def list_robots(
    session: SessionDep,
    zone_id: uuid.UUID | None = None,
    status: RobotStatus | None = None,
):
    """List all robots, optionally filtered by zone and/or status."""
    fm = FleetManager(session)
    robots = await fm.list_robots(zone_id=zone_id, status=status)
    return robots


@router.get("/robots/{robot_id}", response_model=RobotOut)
async def get_robot(robot_id: uuid.UUID, session: SessionDep):
    """Get a single robot by ID."""
    fm = FleetManager(session)
    try:
        robot = await fm.get_robot(robot_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Robot not found")
    return robot


# ---------------------------------------------------------------------------
# Grid endpoint
# ---------------------------------------------------------------------------


@router.get("/grid")
async def get_grid(
    session: SessionDep,
    zone_id: uuid.UUID | None = None,
):
    """Return the current grid state for a zone.

    Returns cells as ``[{row, col, type}, ...]`` for non-FLOOR cells.
    If a seeded/preset grid exists in ``simulation_state``, it takes precedence.
    """

    def _grid_to_cells(grid_2d: list[list[CellType]], zid: str | None = None):
        rows = len(grid_2d)
        cols = len(grid_2d[0]) if rows > 0 else 0
        cells = []
        for r in range(rows):
            for c in range(cols):
                ct = grid_2d[r][c]
                ct_val = ct.value if hasattr(ct, "value") else ct
                if ct_val != "FLOOR":
                    cells.append({"row": r, "col": c, "type": ct_val})
        result: dict = {"rows": rows, "cols": cols, "cells": cells}
        if zid is not None:
            result["zone_id"] = zid
        return result

    # Prefer the seeded / preset grid when available.
    if simulation_state.grid is not None:
        return _grid_to_cells(
            simulation_state.grid,
            str(zone_id) if zone_id else None,
        )

    if zone_id is not None:
        zm = ZoneManager(session)
        try:
            zone = await zm.get_zone(zone_id)
        except ValueError:
            # Zone was deleted (e.g. after reset) — return empty grid instead of 404.
            return {"rows": 0, "cols": 0, "cells": []}
        return {"zone_id": str(zone_id), "rows": zone.grid_rows, "cols": zone.grid_cols, "cells": []}

    return {"rows": 0, "cols": 0, "cells": []}


# ---------------------------------------------------------------------------
# Zone endpoints
# ---------------------------------------------------------------------------


@router.get("/zones", response_model=list[ZoneOut])
async def list_zones(session: SessionDep):
    """List all zones."""
    zm = ZoneManager(session)
    return await zm.list_zones()


@router.get("/zones/{zone_id}", response_model=ZoneOut)
async def get_zone(zone_id: uuid.UUID, session: SessionDep):
    """Get a single zone by ID."""
    zm = ZoneManager(session)
    try:
        zone = await zm.get_zone(zone_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


# ---------------------------------------------------------------------------
# Flow request endpoint (WES -> ESS)
# ---------------------------------------------------------------------------


@router.post("/flows/request")
async def request_flow(body: FlowRequest, session: SessionDep):
    """WES -> ESS flow request: triggers RetrieveSourceTote event."""
    from src.shared.event_bus import event_bus
    from src.wes.domain.events import RetrieveSourceTote

    if body.source_tote_id and body.from_location:
        await event_bus.publish(RetrieveSourceTote(
            pick_task_id=body.pick_task_id,
            tote_id=body.source_tote_id,
            source_location_id=body.from_location,
            station_id=body.station_id,
        ))
        return {"status": "flow_requested", "pick_task_id": str(body.pick_task_id)}

    return {"status": "no_action", "detail": "Missing source_tote_id or from_location"}


# ---------------------------------------------------------------------------
# Simulation endpoints
# ---------------------------------------------------------------------------


@router.post("/simulation/start")
async def simulation_start(session: SessionDep):
    """Start the simulation engine."""
    engine = _get_engine()
    if engine.running:
        return {"status": "already_running"}

    traffic = simulation_state.traffic
    redis_client = await get_redis()
    cache = RobotStateCache(redis_client)
    fm = FleetManager(session)

    planner = PathPlanner(simulation_state.grid) if simulation_state.grid else None

    global _simulator
    _simulator = RobotSimulator(
        fm, traffic, cache,
        grid=simulation_state.grid,
        path_planner=planner,
    )
    engine.register_updatable(_simulator.update)

    # Register WES-driven updatables when in WES mode (skip in interactive mode).
    if simulation_state.wes_driven and not simulation_state.interactive_mode:
        from src.ess.simulation.order_generator import OrderGenerator
        from src.ess.simulation.station_operator import StationOperator

        # Cap active orders to the number of robot pairs (min of A42TD, K50H).
        robots = await fm.list_robots()
        a42td_count = sum(1 for r in robots if r.type.value == "A42TD")
        k50h_count = sum(1 for r in robots if r.type.value == "K50H")
        max_active = min(a42td_count, k50h_count) or 2

        order_gen = OrderGenerator(
            orders_per_minute=simulation_state.order_rate,
            zone_id=simulation_state.zone_id,
            max_active_orders=max_active,
        )
        station_op = StationOperator(
            processing_ticks=simulation_state.station_processing_ticks,
        )
        engine.register_updatable(order_gen.update)
        engine.register_updatable(station_op.update)

    await engine.start()
    return {
        "status": "started",
        "wes_driven": simulation_state.wes_driven,
        "interactive_mode": simulation_state.interactive_mode,
    }


@router.post("/simulation/pause")
async def simulation_pause():
    """Pause the simulation engine."""
    engine = _get_engine()
    engine.pause()
    return {"status": "paused"}


@router.post("/simulation/resume")
async def simulation_resume():
    """Resume the simulation engine."""
    engine = _get_engine()
    engine.resume()
    return {"status": "resumed"}


@router.post("/simulation/speed")
async def simulation_set_speed(body: SpeedRequest):
    """Set the simulation speed multiplier."""
    engine = _get_engine()
    engine.set_speed(body.speed)
    return {"status": "ok", "speed": engine.speed}


@router.post("/simulation/step")
async def simulation_step():
    """Execute a single manual tick (only effective while paused)."""
    engine = _get_engine()
    await engine.step()
    return {"status": "stepped", "elapsed_ticks": engine.elapsed_ticks}


@router.post("/simulation/reset")
async def simulation_reset(session: SessionDep):
    """Stop the engine and reset all simulation state + DB data."""
    global _engine, _simulator
    if _engine is not None:
        await _engine.stop()
    _engine = None
    _simulator = None
    simulation_state.reset()

    # Clear DB (same cascade as preset apply).
    from src.ess.domain.models import (
        EquipmentTask, Location, Robot, Tote, Zone as ZoneModel,
    )
    from src.wes.domain.models import (
        Inventory, Order, PickTask, PutWallSlot, Station,
    )
    from sqlalchemy import delete

    await session.execute(delete(EquipmentTask))
    await session.execute(delete(PickTask))
    await session.execute(delete(Order))
    await session.execute(delete(PutWallSlot))
    await session.execute(delete(Inventory))
    await session.execute(delete(Tote))
    await session.execute(delete(Location))
    await session.execute(delete(Robot))
    await session.execute(delete(Station))
    await session.execute(delete(ZoneModel))
    await session.commit()

    # Clear Redis.
    try:
        redis_client = await get_redis()
        cache = RobotStateCache(redis_client)
        await cache.clear_all()
    except Exception:
        pass

    return {"status": "reset"}


@router.get("/simulation/debug")
async def simulation_debug():
    """Debug: show in-memory robot state and Redis paths."""
    redis_client = await get_redis()
    cache = RobotStateCache(redis_client)

    robots_info = []
    if _simulator is not None and _simulator._robots is not None:
        for r in _simulator._robots:
            path = await cache.get_path(r.id)
            redis_state = await cache.get_state(r.id)
            robots_info.append({
                "name": r.name,
                "mem_pos": f"({r.grid_row},{r.grid_col})",
                "mem_status": r.status.value if hasattr(r.status, 'value') else str(r.status),
                "redis_pos": f"({redis_state.get('row','-')},{redis_state.get('col','-')})" if redis_state else "N/A",
                "redis_status": redis_state.get("status", "N/A") if redis_state else "N/A",
                "path_len": len(path),
                "path_first3": path[:3] if path else [],
            })
    # Query DB for orders, equipment tasks, and robot DB statuses.
    from src.shared.database import async_session_factory
    from sqlalchemy import select, func
    db_info = {}
    try:
        async with async_session_factory() as db_session:
            from src.wes.domain.models import Order, PickTask
            from src.ess.domain.models import EquipmentTask, Robot

            # Order counts by status
            result = await db_session.execute(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
            db_info["orders"] = {str(row[0].value) if hasattr(row[0], 'value') else str(row[0]): row[1] for row in result.all()}

            # EquipmentTask counts by state
            result = await db_session.execute(
                select(EquipmentTask.state, func.count(EquipmentTask.id)).group_by(EquipmentTask.state)
            )
            db_info["eq_tasks"] = {str(row[0].value) if hasattr(row[0], 'value') else str(row[0]): row[1] for row in result.all()}

            # Robot DB statuses
            result = await db_session.execute(select(Robot.name, Robot.status, Robot.grid_row, Robot.grid_col))
            db_info["robots_db"] = [
                {"name": row[0], "status": row[1].value if hasattr(row[1], 'value') else str(row[1]), "pos": f"({row[2]},{row[3]})"}
                for row in result.all()
            ]
    except Exception as exc:
        db_info["error"] = str(exc)

    return {"robots": robots_info, "db": db_info, "elapsed_ticks": _get_engine().elapsed_ticks}


@router.get("/simulation/config")
async def simulation_config():
    """Return the current simulation configuration."""
    engine = _get_engine()
    return {
        "running": engine.running,
        "paused": engine.paused,
        "speed": engine.speed,
        "elapsed_ticks": engine.elapsed_ticks,
        "presets": SimulationPresets.list_presets(),
        "wes_driven": simulation_state.wes_driven,
        "interactive_mode": simulation_state.interactive_mode,
        "order_rate": simulation_state.order_rate,
        "station_processing_ticks": simulation_state.station_processing_ticks,
    }


@router.post("/simulation/presets/apply")
async def simulation_apply_preset(body: PresetRequest, session: SessionDep):
    """Apply a simulation preset (creates zone, robots, and WES data)."""
    try:
        preset = SimulationPresets.get_preset(body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Stop running simulation before applying a new preset.
    global _engine, _simulator
    if _engine is not None:
        await _engine.stop()
    _engine = None
    _simulator = None
    simulation_state.reset()

    # Cascade-delete all existing data to avoid UNIQUE constraint conflicts.
    from src.ess.domain.models import (
        EquipmentTask, Location, Robot, Tote, Zone as ZoneModel,
    )
    from src.wes.domain.models import (
        Inventory, Order, PickTask, PutWallSlot, Station,
    )
    from sqlalchemy import delete

    # Order matters for FK constraints (children before parents).
    await session.execute(delete(EquipmentTask))
    await session.execute(delete(PickTask))
    await session.execute(delete(Order))
    await session.execute(delete(PutWallSlot))
    await session.execute(delete(Inventory))
    await session.execute(delete(Tote))
    await session.execute(delete(Location))
    await session.execute(delete(Robot))
    await session.execute(delete(Station))
    await session.execute(delete(ZoneModel))
    await session.flush()

    # Also clear Redis robot paths.
    try:
        redis_client = await get_redis()
        cache = RobotStateCache(redis_client)
        await cache.clear_all()
    except Exception:
        pass  # Redis may not be available

    zm = ZoneManager(session)
    zone_cfg = preset["zone"]
    zone = await zm.create_zone(zone_cfg["name"], zone_cfg["rows"], zone_cfg["cols"])

    # Build grid config from preset.
    grid_config: dict[str, list[list[int]]] = {
        "walls": [],
        "racks": [],
        "stations": [],
        "aisles": [],
        "charging": [],
    }

    rack_cfg = preset.get("racks", {})
    rack_positions: list[tuple[int, int]] = []
    for r in rack_cfg.get("rows", []):
        for c in rack_cfg.get("cols", []):
            grid_config["racks"].append([r, c])
            rack_positions.append((r, c))

    # Store rack_edge_row from preset into simulation_state.
    rack_edge_row = preset.get("rack_edge_row")
    simulation_state.rack_edge_row = rack_edge_row

    for st in preset.get("stations", []):
        grid_config["stations"].append([st["row"], st["col"]])

    # Extra wall cells from preset (bottleneck corridor, etc.).
    for w in preset.get("walls", []):
        grid_config["walls"].append(w)

    # Rack blocks (crosstraffic preset).
    for block in preset.get("rack_blocks", []):
        r0, c0, sz = block["r0"], block["c0"], block["size"]
        for dr in range(sz):
            for dc in range(sz):
                grid_config["racks"].append([r0 + dr, c0 + dc])
                rack_positions.append((r0 + dr, c0 + dc))

    # Dense rack rows (dense preset).
    dense = preset.get("dense_racks")
    if dense:
        for r in range(dense["start_row"], dense["end_row"], dense["row_step"]):
            for dr in range(dense["rack_depth"]):
                if r + dr < zone_cfg["rows"] - 1:
                    for c in range(dense["col_start"], dense["col_end"]):
                        if c % dense["vertical_aisle_every"] != 0:
                            grid_config["racks"].append([r + dr, c])
                            rack_positions.append((r + dr, c))

    simulation_state.grid = await zm.build_grid(zone.id, grid_config)

    # Set flags from preset.
    interactive = preset.get("interactive_mode", False)
    wes_driven = preset.get("wes_driven", False)
    # Interactive mode requires WES — force it on, disable auto-dispatch.
    if interactive:
        wes_driven = True
    simulation_state.wes_driven = wes_driven
    simulation_state.interactive_mode = interactive
    simulation_state.auto_dispatch = (
        preset.get("auto_dispatch", False) and not wes_driven and not interactive
    )
    simulation_state.zone_id = zone.id
    if wes_driven:
        simulation_state.order_rate = preset.get("orders_per_minute", 6.0)
        simulation_state.station_processing_ticks = preset.get(
            "station_processing_ticks", 5
        )

    # Register robots on FLOOR cells near the top of the grid.
    fm = FleetManager(session)
    robot_cfg = preset.get("robots", {})
    a42td_count = robot_cfg.get("a42td_count", 0)
    k50h_count = robot_cfg.get("k50h_count", 0)

    # Collect FLOOR cells for safe robot placement.
    floor_cells: list[tuple[int, int]] = []
    if simulation_state.grid:
        for r in range(len(simulation_state.grid)):
            for c in range(len(simulation_state.grid[0])):
                if simulation_state.grid[r][c] == CellType.FLOOR:
                    floor_cells.append((r, c))

    placed = 0
    for i in range(a42td_count):
        if placed < len(floor_cells):
            r, c = floor_cells[placed]
        else:
            r, c = 0, i
        await fm.register_robot(
            name=f"A42TD-{i+1:03d}",
            type=RobotType.A42TD,
            zone_id=zone.id,
            row=r,
            col=c,
        )
        placed += 1
    for i in range(k50h_count):
        if placed < len(floor_cells):
            r, c = floor_cells[placed]
        else:
            r, c = 1, i
        await fm.register_robot(
            name=f"K50H-{i+1:03d}",
            type=RobotType.K50H,
            zone_id=zone.id,
            row=r,
            col=c,
        )
        placed += 1

    # ------------------------------------------------------------------
    # WES data: Stations, Locations, Totes, Inventory
    # Only created when preset has wes_driven=True
    # ------------------------------------------------------------------
    totes_created = 0
    stations_created = 0

    if wes_driven:
        from collections import defaultdict

        station_records: list[Station] = []
        locations: list[Location] = []

        # Create Station records + PutWallSlots.
        import json as _json
        for idx, st_cfg in enumerate(preset.get("stations", [])):
            station = Station(
                name=f"STN-{idx + 1:02d}",
                zone_id=zone.id,
                grid_row=st_cfg["row"],
                grid_col=st_cfg["col"],
            )
            # Set queue cell positions from preset
            approach = st_cfg.get("approach_cell")
            if approach:
                station.approach_cell_row = approach["row"]
                station.approach_cell_col = approach["col"]
            holding = st_cfg.get("holding_cell")
            if holding:
                station.holding_cell_row = holding["row"]
                station.holding_cell_col = holding["col"]
            queue_cells = st_cfg.get("queue_cells")
            if queue_cells:
                station.queue_cells_json = _json.dumps(queue_cells)

            session.add(station)
            await session.flush()
            station_records.append(station)

            # Create 6 put-wall slots per station.
            for slot_num in range(1, 7):
                slot = PutWallSlot(
                    station_id=station.id,
                    slot_label=f"S{idx+1}-{slot_num:02d}",
                )
                session.add(slot)

        await session.flush()
        stations_created = len(station_records)

        # Create Locations for rack cells.
        for i, (r, c) in enumerate(rack_positions):
            loc = Location(
                label=f"RACK-R{r:02d}C{c:02d}",
                zone_id=zone.id,
                rack_id=f"RACK-R{r:02d}C{c:02d}",
                floor=1,
                grid_row=r,
                grid_col=c,
            )
            session.add(loc)
            locations.append(loc)

        # Create Locations for station cells.
        for station in station_records:
            loc = Location(
                label=f"STN-{station.name}",
                zone_id=zone.id,
                grid_row=station.grid_row,
                grid_col=station.grid_col,
            )
            session.add(loc)
            locations.append(loc)

        await session.flush()

        # Create Totes on rack locations.
        rack_locations = [loc for loc in locations if loc.rack_id is not None]
        sku_count = preset.get("sku_count", 10)
        totes_per_slot = preset.get("totes_per_rack_slot", 1)
        max_totes = preset.get("totes", len(rack_locations) * totes_per_slot)
        sku_quantities: dict[str, int] = defaultdict(int)

        for i, loc in enumerate(rack_locations[:max_totes]):
            sku_num = (i % sku_count) + 1
            sku = f"SKU-{sku_num:03d}"
            qty = 10

            tote = Tote(
                barcode=f"TOTE-{i+1:04d}",
                sku=sku,
                quantity=qty,
                current_location_id=loc.id,
                home_location_id=loc.id,
            )
            session.add(tote)
            await session.flush()

            loc.tote_id = tote.id
            sku_quantities[sku] += qty
            totes_created += 1

        await session.flush()

        # Create Inventory records with sku_name and band metadata.
        _SKU_NAMES = [
            "Widget Alpha", "Gizmo Beta", "Sprocket Gamma", "Flange Delta",
            "Coupler Epsilon", "Bracket Zeta", "Gasket Eta", "Bearing Theta",
            "Piston Iota", "Valve Kappa", "Nozzle Lambda", "Sleeve Mu",
            "Collar Nu", "Bushing Xi", "Anchor Omicron", "Rivet Pi",
            "Washer Rho", "Bolt Sigma", "Nut Tau", "Pin Upsilon",
            "Cam Phi", "Gear Chi", "Shaft Psi", "Hub Omega",
            "Drum Alpha-2", "Lever Beta-2", "Hinge Gamma-2", "Axle Delta-2",
            "Spring Epsilon-2", "Clamp Zeta-2",
        ]
        _BANDS = ["A", "B", "C", "D", "E", "F"]
        for sku, total_qty in sku_quantities.items():
            # Extract numeric part for cycling names/bands
            sku_num = int(sku.split("-")[1]) - 1
            inv = Inventory(
                sku=sku,
                sku_name=_SKU_NAMES[sku_num % len(_SKU_NAMES)],
                band=_BANDS[sku_num % len(_BANDS)],
                zone_id=zone.id,
                total_qty=total_qty,
                allocated_qty=0,
            )
            session.add(inv)

        await session.flush()

    # Apply speed from preset.
    engine = _get_engine()
    engine.set_speed(preset.get("speed", 1.0))

    await session.commit()

    # Broadcast a fresh snapshot so all connected WS clients see the new robots.
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    logger.info(
        "Preset '%s' applied: zone=%s, robots=%d (A42TD=%d, K50H=%d), "
        "stations=%d, totes=%d, snapshot_robots=%d, snapshot_stations=%d",
        body.name, zone.id, a42td_count + k50h_count,
        a42td_count, k50h_count, stations_created, totes_created,
        len(snapshot.get("robots", {})),
        len(snapshot.get("stations", [])),
    )
    await ws_manager.broadcast("snapshot", snapshot)

    return {
        "status": "applied",
        "preset": body.name,
        "zone_id": str(zone.id),
        "robots_created": a42td_count + k50h_count,
        "totes": totes_created,
        "stations_created": stations_created,
        "wes_driven": wes_driven,
        "interactive_mode": simulation_state.interactive_mode,
    }


@router.post("/simulation/presets/custom")
async def simulation_apply_custom_preset(
    body: CustomPresetRequest, session: SessionDep,
):
    """Apply a user-configured custom preset."""
    # Build station list from explicit stations or station_count.
    if body.stations:
        stations_list = body.stations
    elif body.station_count > 0:
        # Auto-place stations evenly across the bottom of the grid.
        row = body.zone_rows - 2
        spacing = max(1, body.zone_cols // (body.station_count + 1))
        stations_list = [
            {"row": row, "col": spacing * (i + 1)}
            for i in range(body.station_count)
        ]
    else:
        stations_list = [
            {"row": body.zone_rows - 2, "col": body.zone_cols // 3},
            {"row": body.zone_rows - 2, "col": body.zone_cols * 2 // 3},
        ]

    # Build a preset dict from the custom parameters.
    preset = {
        "description": "Custom preset",
        "zone": {"name": "custom", "rows": body.zone_rows, "cols": body.zone_cols},
        "robots": {
            "a42td_count": body.a42td_count,
            "k50h_count": body.k50h_count,
        },
        "totes": body.totes,
        "racks": {
            "rows": range(body.rack_row_start, body.rack_edge_row + 1),
            "cols": range(body.rack_col_start, body.rack_col_end),
        },
        "rack_edge_row": body.rack_edge_row,
        "stations": stations_list,
        "tick_interval_ms": 150,
        "speed": body.speed,
        "wes_driven": body.wes_driven or body.interactive_mode,
        "interactive_mode": body.interactive_mode,
        "auto_dispatch": not body.wes_driven and not body.interactive_mode,
        "orders_per_minute": body.orders_per_minute,
        "station_processing_ticks": body.station_processing_ticks,
        "sku_count": body.sku_count,
        "totes_per_rack_slot": 1,
    }

    # Reuse the apply logic by calling simulation_apply_preset internally.
    fake_request = PresetRequest(name="__custom__")

    # Temporarily register the custom preset.
    from src.ess.simulation.presets import _PRESETS
    _PRESETS["__custom__"] = preset
    try:
        result = await simulation_apply_preset(fake_request, session)
        result["preset"] = "custom"
        return result
    finally:
        _PRESETS.pop("__custom__", None)


# ---------------------------------------------------------------------------
# Grid editor endpoints
# ---------------------------------------------------------------------------


class GridSaveRequest(BaseModel):
    name: str
    rows: int
    cols: int
    cells: list[dict]  # [{"row": int, "col": int, "type": str}, ...]


class GridCellUpdate(BaseModel):
    row: int
    col: int
    cell_type: str


@router.post("/grid/save")
async def grid_save(body: GridSaveRequest):
    """Save a grid layout to a JSON file."""
    import json
    from pathlib import Path

    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    layouts_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c for c in body.name if c.isalnum() or c in "-_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid layout name")

    path = layouts_dir / f"{safe_name}.json"
    data = {
        "name": body.name,
        "rows": body.rows,
        "cols": body.cols,
        "cells": body.cells,
    }
    path.write_text(json.dumps(data, indent=2))
    return {"status": "saved", "name": safe_name}


@router.get("/grid/layouts")
async def grid_list_layouts():
    """List all saved grid layouts."""
    import json
    from pathlib import Path

    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    if not layouts_dir.exists():
        return {"layouts": []}

    layouts = []
    for p in sorted(layouts_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            layouts.append({
                "name": data.get("name", p.stem),
                "file": p.stem,
                "rows": data.get("rows", 0),
                "cols": data.get("cols", 0),
            })
        except Exception:
            continue
    return {"layouts": layouts}


@router.get("/grid/layouts/{name}")
async def grid_load_layout(name: str):
    """Load a specific grid layout."""
    import json
    from pathlib import Path

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    path = layouts_dir / f"{safe_name}.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="Layout not found")

    data = json.loads(path.read_text())
    return data


@router.post("/grid/load/{name}")
async def grid_load_into(name: str):
    """Load a saved layout and apply it to the current in-memory grid (bulk)."""
    import json
    from pathlib import Path

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    path = layouts_dir / f"{safe_name}.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="Layout not found")

    data = json.loads(path.read_text())
    rows = data.get("rows", 0)
    cols = data.get("cols", 0)
    cells = data.get("cells", [])

    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="Invalid layout dimensions")

    # Build a fresh grid from the layout
    new_grid: list[list[CellType]] = [
        [CellType.FLOOR for _ in range(cols)] for _ in range(rows)
    ]
    for cell in cells:
        r, c = cell.get("row", -1), cell.get("col", -1)
        ct = cell.get("type", "FLOOR")
        if 0 <= r < rows and 0 <= c < cols:
            try:
                new_grid[r][c] = CellType(ct)
            except ValueError:
                pass  # skip unknown cell types

    simulation_state.grid = new_grid
    return {"status": "loaded", "name": safe_name, "rows": rows, "cols": cols}


@router.post("/grid/cell")
async def grid_update_cell(body: GridCellUpdate):
    """Update a single cell in the current in-memory grid (editor mode)."""
    if simulation_state.grid is None:
        raise HTTPException(status_code=400, detail="No grid loaded")

    rows = len(simulation_state.grid)
    cols = len(simulation_state.grid[0]) if rows > 0 else 0

    if body.row < 0 or body.row >= rows or body.col < 0 or body.col >= cols:
        raise HTTPException(status_code=400, detail="Cell out of bounds")

    try:
        cell_type = CellType(body.cell_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cell type: {body.cell_type}. "
            f"Valid: {[ct.value for ct in CellType]}",
        )

    simulation_state.grid[body.row][body.col] = cell_type
    return {"status": "ok", "row": body.row, "col": body.col, "type": cell_type.value}


@router.post("/grid/resize")
async def grid_resize(rows: int, cols: int):
    """Resize the current in-memory grid, preserving existing cells."""
    if rows < 5 or cols < 5:
        raise HTTPException(status_code=400, detail="Grid must be at least 5x5")
    if rows > 200 or cols > 200:
        raise HTTPException(status_code=400, detail="Grid too large (max 200x200)")

    old = simulation_state.grid
    new_grid: list[list[CellType]] = [
        [CellType.FLOOR for _ in range(cols)] for _ in range(rows)
    ]

    if old is not None:
        old_rows = len(old)
        old_cols = len(old[0]) if old_rows > 0 else 0
        for r in range(min(rows, old_rows)):
            for c in range(min(cols, old_cols)):
                new_grid[r][c] = old[r][c]

    simulation_state.grid = new_grid
    return {"status": "ok", "rows": rows, "cols": cols}
