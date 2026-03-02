"""FastAPI router for the Equipment Scheduling System (ESS)."""

from __future__ import annotations

import logging
import random
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
    territory_col_min: int | None = None
    territory_col_max: int | None = None
    territory_row_min: int | None = None
    territory_row_max: int | None = None

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


def _overlay_live_position(robot) -> dict:
    """Build a RobotOut-compatible dict, overlaying live simulation positions.

    When no live data exists AND the DB position is (0, 0), we still return
    the robot but log a warning — the default (0,0) is almost certainly stale.
    """
    import src.shared.simulation_state as _sim
    rid = str(robot.id)
    live = _sim.robot_positions.get(rid)
    # Determine the best-known row/col.  Prefer live; fall back to DB only
    # when the DB values are NOT the uninitialised (0,0) default.
    if live:
        row = live["row"]
        col = live["col"]
        heading = live.get("heading", robot.heading)
        status = live["status"]
    else:
        row = robot.grid_row
        col = robot.grid_col
        heading = robot.heading
        status = robot.status.value if hasattr(robot.status, "value") else str(robot.status)
    return {
        "id": robot.id,
        "name": robot.name,
        "type": robot.type.value if hasattr(robot.type, "value") else str(robot.type),
        "zone_id": robot.zone_id,
        "status": status,
        "grid_row": row,
        "grid_col": col,
        "heading": heading,
        "current_task_id": robot.current_task_id,
        "territory_col_min": getattr(robot, "territory_col_min", None),
        "territory_col_max": getattr(robot, "territory_col_max", None),
        "territory_row_min": getattr(robot, "territory_row_min", None),
        "territory_row_max": getattr(robot, "territory_row_max", None),
    }


@router.get("/robots")
async def list_robots(
    session: SessionDep,
    zone_id: uuid.UUID | None = None,
    status: RobotStatus | None = None,
):
    """List all robots, optionally filtered by zone and/or status."""
    fm = FleetManager(session)
    robots = await fm.list_robots(zone_id=zone_id, status=status)
    return [_overlay_live_position(r) for r in robots]


@router.get("/robots/{robot_id}")
async def get_robot(robot_id: uuid.UUID, session: SessionDep):
    """Get a single robot by ID."""
    fm = FleetManager(session)
    try:
        robot = await fm.get_robot(robot_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Robot not found")
    return _overlay_live_position(robot)


class CreateRobotRequest(BaseModel):
    type: str  # "K50H" or "A42TD"
    row: int
    col: int


@router.post("/robots")
async def create_robot(body: CreateRobotRequest, session: SessionDep):
    """Create a single robot at the given grid position (editor mode)."""
    from sqlalchemy import select as sa_select, func as sa_func

    from src.ess.application.zone_manager import ZoneManager
    from src.ess.domain.models import Robot, Zone as ZoneModel

    try:
        robot_type = RobotType(body.type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid robot type: {body.type}")

    # Ensure a zone exists.
    if simulation_state.zone_id is None:
        if simulation_state.grid is None:
            raise HTTPException(status_code=400, detail="No grid loaded")
        rows = len(simulation_state.grid)
        cols = len(simulation_state.grid[0]) if rows > 0 else 0
        zm = ZoneManager(session)
        zone = await zm.create_zone("Editor", rows, cols)
        simulation_state.zone_id = zone.id
        simulation_state.wes_driven = True
        simulation_state.interactive_mode = True
        await session.flush()

    zone_id = simulation_state.zone_id

    # Auto-generate name based on existing count for this type.
    count_result = await session.execute(
        sa_select(sa_func.count()).select_from(Robot).where(Robot.type == robot_type)
    )
    count = (count_result.scalar() or 0) + 1
    name = f"{robot_type.value}-{count:03d}"

    fm = FleetManager(session)
    robot = await fm.register_robot(
        name=name,
        type=robot_type,
        zone_id=zone_id,
        row=body.row,
        col=body.col,
    )
    await session.commit()

    # Immediately register in simulation_state so snapshot_builder and REST
    # endpoints never fall back to stale DB defaults (0,0).
    rid = str(robot.id)
    _rt = robot.type.value if hasattr(robot.type, "value") else str(robot.type)
    _st = robot.status.value if hasattr(robot.status, "value") else str(robot.status)
    simulation_state.robot_positions[rid] = {
        "row": body.row,
        "col": body.col,
        "heading": 0,
        "status": _st,
    }

    # Broadcast snapshot so robot appears on all clients.
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    await ws_manager.broadcast("snapshot", snapshot)

    return {
        "status": "created",
        "id": str(robot.id),
        "name": robot.name,
        "type": robot.type.value,
        "row": body.row,
        "col": body.col,
    }


@router.delete("/robots/{robot_id}")
async def delete_robot(robot_id: uuid.UUID, session: SessionDep):
    """Delete a single robot."""
    from src.ess.domain.models import Robot

    robot = await session.get(Robot, robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="Robot not found")

    await session.delete(robot)
    await session.commit()

    # Broadcast snapshot so robot disappears on all clients.
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    await ws_manager.broadcast("snapshot", snapshot)

    return {"status": "deleted", "id": str(robot_id)}


class UpdateTerritoryRequest(BaseModel):
    col_min: int | None = None
    col_max: int | None = None
    row_min: int | None = None
    row_max: int | None = None


@router.put("/robots/{robot_id}/territory")
async def update_robot_territory(
    robot_id: uuid.UUID, body: UpdateTerritoryRequest, session: SessionDep
):
    """Set or clear the territory grid rectangle for an A42TD robot."""
    from src.ess.domain.models import Robot

    robot = await session.get(Robot, robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="Robot not found")

    robot.territory_col_min = body.col_min
    robot.territory_col_max = body.col_max
    robot.territory_row_min = body.row_min
    robot.territory_row_max = body.row_max
    await session.commit()

    # Broadcast snapshot so all clients see updated territory.
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    await ws_manager.broadcast("snapshot", snapshot)

    return {
        "status": "updated",
        "id": str(robot_id),
        "territory_col_min": body.col_min,
        "territory_col_max": body.col_max,
        "territory_row_min": body.row_min,
        "territory_row_max": body.row_max,
    }


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

    # Ensure idle_points are up-to-date from current grid.
    if simulation_state.grid and not simulation_state.idle_points:
        _ip: list[tuple[int, int]] = []
        for r in range(len(simulation_state.grid)):
            for c in range(len(simulation_state.grid[0])):
                if simulation_state.grid[r][c] == CellType.IDLE_POINT:
                    _ip.append((r, c))
        simulation_state.idle_points = _ip

    # Ensure rack_edge_row is set (auto-detect from grid if missing).
    if simulation_state.rack_edge_row is None and simulation_state.grid:
        _max_rack_row = -1
        for r in range(len(simulation_state.grid)):
            for c in range(len(simulation_state.grid[0])):
                if simulation_state.grid[r][c] == CellType.RACK:
                    _max_rack_row = max(_max_rack_row, r)
        if _max_rack_row >= 0:
            _rows = len(simulation_state.grid)
            simulation_state.rack_edge_row = min(_max_rack_row + 1, _rows - 1)

    # Ensure aisle_rows is set.
    if simulation_state.grid and not simulation_state.aisle_rows:
        _ar: set[int] = set()
        _rows = len(simulation_state.grid)
        _cols = len(simulation_state.grid[0]) if _rows > 0 else 0
        for r in range(_rows):
            has_floor = any(simulation_state.grid[r][c] == CellType.FLOOR for c in range(_cols))
            if has_floor:
                adj_rack = False
                if r > 0 and any(simulation_state.grid[r - 1][c] == CellType.RACK for c in range(_cols)):
                    adj_rack = True
                if r < _rows - 1 and any(simulation_state.grid[r + 1][c] == CellType.RACK for c in range(_cols)):
                    adj_rack = True
                if adj_rack:
                    _ar.add(r)
        simulation_state.aisle_rows = _ar

    planner = PathPlanner(simulation_state.grid) if simulation_state.grid else None

    # Pre-load robots while the DB session is still alive.
    # The session closes when this endpoint returns, so the simulator
    # must NOT lazy-load robots from a dead session on the first tick.
    # We expunge the robots from the session so they become detached but
    # retain their attribute values (prevents lazy-load from dead session).
    preloaded_robots = await fm.list_robots()
    for _r in preloaded_robots:
        # Materialize key attributes before detaching.
        _ = _r.id, _r.name, _r.type, _r.grid_row, _r.grid_col, _r.heading, _r.status, _r.zone_id
        _ = _r.reserved, _r.reservation_order_id, _r.reservation_pick_task_id, _r.reservation_station_id
        _ = _r.hold_pick_task_id, _r.hold_at_station
        _ = _r.territory_col_min, _r.territory_col_max, _r.territory_row_min, _r.territory_row_max
        session.expunge(_r)

    global _simulator
    _simulator = RobotSimulator(
        fm, traffic, cache,
        grid=simulation_state.grid,
        path_planner=planner,
        preloaded_robots=preloaded_robots,
    )
    engine.register_updatable(_simulator.update)

    # Register WES-driven updatables when in WES mode (skip in interactive mode).
    if simulation_state.wes_driven and not simulation_state.interactive_mode:
        from src.ess.simulation.order_generator import OrderGenerator
        from src.ess.simulation.station_operator import StationOperator

        # Cap active orders to the number of robot pairs (min of A42TD, K50H).
        robots = preloaded_robots
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

    # Broadcast empty snapshot so frontend clears all state
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    await ws_manager.broadcast("snapshot", snapshot)

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

    # Compute aisle rows: FLOOR rows adjacent to at least one RACK row.
    _grid = simulation_state.grid
    if _grid:
        _aisle_rows: set[int] = set()
        _nrows = len(_grid)
        _ncols = len(_grid[0]) if _nrows > 0 else 0
        for r in range(_nrows):
            has_floor = any(_grid[r][c] == CellType.FLOOR for c in range(_ncols))
            if not has_floor:
                continue
            has_rack_above = (
                r > 0
                and any(_grid[r - 1][c] == CellType.RACK for c in range(_ncols))
            )
            has_rack_below = (
                r < _nrows - 1
                and any(_grid[r + 1][c] == CellType.RACK for c in range(_ncols))
            )
            if has_rack_above or has_rack_below:
                _aisle_rows.add(r)
        simulation_state.aisle_rows = _aisle_rows
    else:
        simulation_state.aisle_rows = set()

    # Collect IDLE_POINT cells for K50H parking.
    if _grid:
        _idle_pts: list[tuple[int, int]] = []
        for r in range(len(_grid)):
            for c in range(len(_grid[0])):
                if _grid[r][c] == CellType.IDLE_POINT:
                    _idle_pts.append((r, c))
        simulation_state.idle_points = _idle_pts
    else:
        simulation_state.idle_points = []

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
            pw_labels = ["A1", "A2", "A3", "B1", "B2", "B3"]
            for pw_label in pw_labels:
                slot = PutWallSlot(
                    station_id=station.id,
                    slot_label=pw_label,
                )
                session.add(slot)

        await session.flush()
        stations_created = len(station_records)

        # Rebuild the queue membership index after stations are created.
        from src.wes.application.station_queue_service import rebuild_queue_index
        rebuild_queue_index(station_records)

        # Create Locations for rack cells (multi-floor: 1=cantilever, 2-10=storage).
        floors_per_rack = preset.get("floors_per_rack", 10)
        for i, (r, c) in enumerate(rack_positions):
            rack_id = f"RACK-R{r:02d}C{c:02d}"
            for floor in range(1, floors_per_rack + 1):
                loc = Location(
                    label=f"{rack_id}-F{floor:02d}",
                    zone_id=zone.id,
                    rack_id=rack_id,
                    floor=floor,
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

        # Create Totes on rack locations (floor > 1 only; floor 1 = cantilever).
        rack_locations = [loc for loc in locations if loc.rack_id is not None and loc.floor > 1]
        random.shuffle(rack_locations)
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

    # Populate robot_positions immediately so snapshot_builder never
    # falls back to stale DB defaults (0,0).
    from sqlalchemy import select as _sel_preset_robots
    _preset_robots = (await session.execute(
        _sel_preset_robots(Robot)
    )).scalars().all()
    _preset_positions: dict[str, dict] = {}
    for _pr in _preset_robots:
        _preset_positions[str(_pr.id)] = {
            "row": _pr.grid_row,
            "col": _pr.grid_col,
            "heading": _pr.heading,
            "status": _pr.status.value if hasattr(_pr.status, "value") else str(_pr.status),
        }
    simulation_state.robot_positions = _preset_positions

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

    # Generate rack rows in groups of 2 with 1-row aisle between each group.
    rack_rows: list[int] = []
    current_row = body.rack_row_start
    while current_row + 1 <= body.rack_row_end:
        rack_rows.append(current_row)
        rack_rows.append(current_row + 1)
        current_row += 3  # 2 rack rows + 1 aisle row
    # rack_edge_row = FLOOR aisle row after the last rack group (cantilever)
    actual_edge_row = (rack_rows[-1] + 1) if rack_rows else body.rack_edge_row

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
            "rows": rack_rows,
            "cols": range(body.rack_col_start, body.rack_col_end),
        },
        "rack_edge_row": actual_edge_row,
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
        "floors_per_rack": 10,
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
async def grid_save(body: GridSaveRequest, session: SessionDep):
    """Save a grid layout to a JSON file, including stations and robots."""
    import json
    from pathlib import Path

    from sqlalchemy import select as sa_select

    from src.ess.domain.models import Robot
    from src.wes.domain.models import Station

    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    layouts_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c for c in body.name if c.isalnum() or c in "-_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid layout name")

    # Query current stations and robots from DB.
    station_rows = (await session.execute(sa_select(Station))).scalars().all()
    robot_rows = (await session.execute(sa_select(Robot))).scalars().all()

    path = layouts_dir / f"{safe_name}.json"
    data = {
        "name": body.name,
        "rows": body.rows,
        "cols": body.cols,
        "cells": body.cells,
        "stations": [
            {
                "name": s.name,
                "grid_row": s.grid_row,
                "grid_col": s.grid_col,
                "approach_cell_row": s.approach_cell_row,
                "approach_cell_col": s.approach_cell_col,
                "holding_cell_row": s.holding_cell_row,
                "holding_cell_col": s.holding_cell_col,
                "queue_cells_json": s.queue_cells_json,
            }
            for s in station_rows
        ],
        "robots": [
            {
                "name": r.name,
                "type": r.type.value,
                # Use live simulation position (source of truth) to avoid
                # saving stale DB defaults (0,0).
                "grid_row": (simulation_state.robot_positions.get(str(r.id), {}).get("row", r.grid_row)),
                "grid_col": (simulation_state.robot_positions.get(str(r.id), {}).get("col", r.grid_col)),
                **({"territory_col_min": r.territory_col_min, "territory_col_max": r.territory_col_max,
                    "territory_row_min": r.territory_row_min, "territory_row_max": r.territory_row_max}
                   if r.territory_col_min is not None else {}),
            }
            for r in robot_rows
        ],
        "config": {
            "wes_driven": simulation_state.wes_driven,
            "interactive_mode": simulation_state.interactive_mode,
            "rack_edge_row": simulation_state.rack_edge_row,
        },
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


@router.delete("/grid/layouts/{name}")
async def grid_delete_layout(name: str):
    """Delete a saved grid layout."""
    from pathlib import Path

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    path = layouts_dir / f"{safe_name}.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="Layout not found")

    path.unlink()
    return {"status": "deleted", "name": safe_name}


@router.post("/grid/load/{name}")
async def grid_load_into(name: str, session: SessionDep):
    """Load a saved layout and fully reconstruct the simulation environment."""
    import json
    from collections import defaultdict
    from pathlib import Path

    from sqlalchemy import delete as sa_delete, select as sa_select

    from src.ess.application.zone_manager import ZoneManager
    from src.ess.domain.models import (
        EquipmentTask, Location, Robot, Tote, Zone as ZoneModel,
    )
    from src.wes.domain.models import (
        Inventory, Order, PickTask, PutWallSlot, Station,
    )

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    layouts_dir = Path(__file__).resolve().parent.parent.parent / "data" / "layouts"
    path = layouts_dir / f"{safe_name}.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="Layout not found")

    data = json.loads(path.read_text())
    rows = data.get("rows", 0)
    cols = data.get("cols", 0)
    cells = data.get("cells", [])
    saved_stations = data.get("stations", [])
    saved_robots = data.get("robots", [])
    config = data.get("config", {})

    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="Invalid layout dimensions")

    # ------------------------------------------------------------------
    # 1. Stop simulation & reset state
    # ------------------------------------------------------------------
    global _engine, _simulator
    if _engine is not None:
        await _engine.stop()
    _engine = None
    _simulator = None
    simulation_state.reset()

    # ------------------------------------------------------------------
    # 2. Cascade-delete all existing DB data
    # ------------------------------------------------------------------
    await session.execute(sa_delete(EquipmentTask))
    await session.execute(sa_delete(PickTask))
    await session.execute(sa_delete(Order))
    await session.execute(sa_delete(PutWallSlot))
    await session.execute(sa_delete(Inventory))
    await session.execute(sa_delete(Tote))
    await session.execute(sa_delete(Location))
    await session.execute(sa_delete(Robot))
    await session.execute(sa_delete(Station))
    await session.execute(sa_delete(ZoneModel))
    await session.flush()

    # Clear Redis robot state.
    try:
        redis_client = await get_redis()
        cache = RobotStateCache(redis_client)
        await cache.clear_all()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3. Create Zone
    # ------------------------------------------------------------------
    zm = ZoneManager(session)
    zone = await zm.create_zone(f"Layout-{safe_name}", rows, cols)

    # ------------------------------------------------------------------
    # 4. Build grid from cells
    # ------------------------------------------------------------------
    new_grid: list[list[CellType]] = [
        [CellType.FLOOR for _ in range(cols)] for _ in range(rows)
    ]
    rack_positions: list[tuple[int, int]] = []
    station_positions: list[tuple[int, int]] = []

    for cell in cells:
        r, c = cell.get("row", -1), cell.get("col", -1)
        ct = cell.get("type", "FLOOR")
        if 0 <= r < rows and 0 <= c < cols:
            try:
                cell_type = CellType(ct)
                new_grid[r][c] = cell_type
                if cell_type == CellType.RACK:
                    rack_positions.append((r, c))
                elif cell_type == CellType.STATION:
                    station_positions.append((r, c))
            except ValueError:
                pass

    simulation_state.grid = new_grid

    # ------------------------------------------------------------------
    # 5. Compute aisle_rows and idle_points
    # ------------------------------------------------------------------
    _aisle_rows: set[int] = set()
    _idle_pts: list[tuple[int, int]] = []
    for r in range(rows):
        has_floor = any(new_grid[r][c] == CellType.FLOOR for c in range(cols))
        if has_floor:
            adj_rack = False
            if r > 0 and any(new_grid[r - 1][c] == CellType.RACK for c in range(cols)):
                adj_rack = True
            if r < rows - 1 and any(new_grid[r + 1][c] == CellType.RACK for c in range(cols)):
                adj_rack = True
            if adj_rack:
                _aisle_rows.add(r)
        for c in range(cols):
            if new_grid[r][c] == CellType.IDLE_POINT:
                _idle_pts.append((r, c))

    simulation_state.aisle_rows = _aisle_rows
    simulation_state.idle_points = _idle_pts

    # ------------------------------------------------------------------
    # 6. Create Robots
    # ------------------------------------------------------------------
    robots_created = 0
    if saved_robots:
        fm = FleetManager(session)
        for robot_cfg in saved_robots:
            r_type = RobotType(robot_cfg["type"])
            robot = await fm.register_robot(
                name=robot_cfg["name"],
                type=r_type,
                zone_id=zone.id,
                row=robot_cfg["grid_row"],
                col=robot_cfg["grid_col"],
            )
            # Restore territory if saved
            if robot_cfg.get("territory_col_min") is not None:
                robot.territory_col_min = robot_cfg["territory_col_min"]
                robot.territory_col_max = robot_cfg.get("territory_col_max")
                robot.territory_row_min = robot_cfg.get("territory_row_min")
                robot.territory_row_max = robot_cfg.get("territory_row_max")
                await session.flush()
            robots_created += 1

    # Immediately populate robot_positions so snapshot_builder never
    # falls back to stale DB defaults (0,0).
    _new_robots_result = (await session.execute(sa_select(Robot))).scalars().all()
    _init_positions: dict[str, dict] = {}
    for _nr in _new_robots_result:
        _init_positions[str(_nr.id)] = {
            "row": _nr.grid_row,
            "col": _nr.grid_col,
            "heading": _nr.heading,
            "status": _nr.status.value if hasattr(_nr.status, "value") else str(_nr.status),
        }
    simulation_state.robot_positions = _init_positions

    # ------------------------------------------------------------------
    # 7. Create Stations (from saved data or auto-detect from grid)
    # ------------------------------------------------------------------
    wes_driven = config.get("wes_driven", True)
    interactive_mode = config.get("interactive_mode", True)

    station_records: list[Station] = []
    locations: list[Location] = []

    if saved_stations:
        # Restore stations from saved layout data
        for st_cfg in saved_stations:
            station = Station(
                name=st_cfg["name"],
                zone_id=zone.id,
                grid_row=st_cfg["grid_row"],
                grid_col=st_cfg["grid_col"],
            )
            if st_cfg.get("approach_cell_row") is not None:
                station.approach_cell_row = st_cfg["approach_cell_row"]
                station.approach_cell_col = st_cfg["approach_cell_col"]
            if st_cfg.get("holding_cell_row") is not None:
                station.holding_cell_row = st_cfg["holding_cell_row"]
                station.holding_cell_col = st_cfg["holding_cell_col"]
            if st_cfg.get("queue_cells_json"):
                station.queue_cells_json = st_cfg["queue_cells_json"]
            session.add(station)
            await session.flush()
            station_records.append(station)

            pw_labels = ["A1", "A2", "A3", "B1", "B2", "B3"]
            for pw_label in pw_labels:
                session.add(PutWallSlot(station_id=station.id, slot_label=pw_label))
    else:
        # Auto-create stations from STATION cells in the grid
        for idx, (sr, sc) in enumerate(station_positions):
            station = Station(
                name=f"STN-{idx + 1:02d}",
                zone_id=zone.id,
                grid_row=sr,
                grid_col=sc,
            )
            session.add(station)
            await session.flush()
            station_records.append(station)

            pw_labels = ["A1", "A2", "A3", "B1", "B2", "B3"]
            for pw_label in pw_labels:
                session.add(PutWallSlot(station_id=station.id, slot_label=pw_label))

    await session.flush()

    # Rebuild queue membership index for freshly-created stations.
    from src.wes.application.station_queue_service import rebuild_queue_index as _rqi
    _rqi(station_records)

    # ------------------------------------------------------------------
    # 8. Create Locations (rack + station)
    # ------------------------------------------------------------------
    floors_per_rack = 10
    for r, c in rack_positions:
        rack_id = f"RACK-R{r:02d}C{c:02d}"
        for floor in range(1, floors_per_rack + 1):
            loc = Location(
                label=f"{rack_id}-F{floor:02d}",
                zone_id=zone.id,
                rack_id=rack_id,
                floor=floor,
                grid_row=r,
                grid_col=c,
            )
            session.add(loc)
            locations.append(loc)

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

    # ------------------------------------------------------------------
    # 9. Create Totes and Inventory
    # ------------------------------------------------------------------
    totes_created = 0
    rack_locations = [loc for loc in locations if loc.rack_id is not None and loc.floor > 1]
    random.shuffle(rack_locations)
    sku_count = 10
    sku_quantities: dict[str, int] = defaultdict(int)

    for i, loc in enumerate(rack_locations):
        sku_num = (i % sku_count) + 1
        sku = f"SKU-{sku_num:03d}"
        qty = 10
        tote = Tote(
            barcode=f"TOTE-{i + 1:04d}",
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

    # ------------------------------------------------------------------
    # 10. Set simulation_state fields
    # ------------------------------------------------------------------
    simulation_state.zone_id = zone.id
    simulation_state.wes_driven = wes_driven
    simulation_state.interactive_mode = interactive_mode

    # rack_edge_row: restore from saved config, or auto-detect from grid.
    saved_rack_edge = config.get("rack_edge_row")
    if saved_rack_edge is not None:
        simulation_state.rack_edge_row = saved_rack_edge
    elif rack_positions:
        # Auto-detect: first FLOOR row below the bottom-most RACK row.
        max_rack_row = max(r for r, _ in rack_positions)
        if max_rack_row + 1 < rows:
            simulation_state.rack_edge_row = max_rack_row + 1
        else:
            simulation_state.rack_edge_row = max_rack_row

    await session.commit()

    # ------------------------------------------------------------------
    # 11. Broadcast snapshot
    # ------------------------------------------------------------------
    from src.shared.snapshot_builder import build_snapshot
    from src.shared.websocket_manager import ws_manager

    snapshot = await build_snapshot()
    await ws_manager.broadcast("snapshot", snapshot)

    return {
        "status": "loaded",
        "name": safe_name,
        "rows": rows,
        "cols": cols,
        "zone_id": str(zone.id),
        "robots_created": robots_created,
        "stations_created": len(station_records),
        "totes_created": totes_created,
    }


@router.post("/grid/cell")
async def grid_update_cell(body: GridCellUpdate, session: SessionDep):
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

    old_type = simulation_state.grid[body.row][body.col]
    simulation_state.grid[body.row][body.col] = cell_type

    # Update idle_points incrementally when IDLE_POINT cells change.
    coord = (body.row, body.col)
    if cell_type == CellType.IDLE_POINT and coord not in simulation_state.idle_points:
        simulation_state.idle_points.append(coord)
    elif old_type == CellType.IDLE_POINT and cell_type != CellType.IDLE_POINT:
        simulation_state.idle_points = [p for p in simulation_state.idle_points if p != coord]

    # ------------------------------------------------------------------
    # STATION cell <-> Station DB record synchronisation
    # ------------------------------------------------------------------
    station_created = None

    if cell_type == CellType.STATION and old_type != CellType.STATION:
        from sqlalchemy import select as sa_select, func as sa_func

        from src.ess.application.zone_manager import ZoneManager
        from src.ess.domain.models import Location, Zone as ZoneModel
        from src.wes.domain.models import PutWallSlot, Station

        # Ensure a zone exists.
        if simulation_state.zone_id is None:
            zm = ZoneManager(session)
            zone = await zm.create_zone("Editor", rows, cols)
            simulation_state.zone_id = zone.id
            simulation_state.wes_driven = True
            simulation_state.interactive_mode = True
            await session.flush()

        zone_id = simulation_state.zone_id

        # Check if a station already exists at this position.
        existing = (
            await session.execute(
                sa_select(Station).where(
                    Station.grid_row == body.row,
                    Station.grid_col == body.col,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # Determine next station name.
            count_result = await session.execute(
                sa_select(sa_func.count()).select_from(Station)
            )
            station_count = count_result.scalar() or 0
            station = Station(
                name=f"STN-{station_count + 1:02d}",
                zone_id=zone_id,
                grid_row=body.row,
                grid_col=body.col,
            )
            session.add(station)
            await session.flush()

            # Create PutWallSlots.
            for pw_label in ["A1", "A2", "A3", "B1", "B2", "B3"]:
                session.add(PutWallSlot(station_id=station.id, slot_label=pw_label))

            # Create station location.
            session.add(Location(
                label=f"STN-{station.name}",
                zone_id=zone_id,
                grid_row=body.row,
                grid_col=body.col,
            ))

            await session.commit()
            station_created = {"id": str(station.id), "name": station.name}

            # Broadcast so frontend station list updates.
            from src.shared.snapshot_builder import build_snapshot
            from src.shared.websocket_manager import ws_manager
            snapshot = await build_snapshot()
            await ws_manager.broadcast("snapshot", snapshot)

    elif old_type == CellType.STATION and cell_type != CellType.STATION:
        from sqlalchemy import select as sa_select, delete as sa_delete

        from src.ess.domain.models import Location
        from src.wes.domain.models import PutWallSlot, Station

        # Find and delete the station at this position.
        existing = (
            await session.execute(
                sa_select(Station).where(
                    Station.grid_row == body.row,
                    Station.grid_col == body.col,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            await session.execute(
                sa_delete(PutWallSlot).where(PutWallSlot.station_id == existing.id)
            )
            await session.execute(
                sa_delete(Location).where(
                    Location.grid_row == body.row,
                    Location.grid_col == body.col,
                    Location.rack_id.is_(None),
                )
            )
            await session.delete(existing)
            await session.commit()

            from src.shared.snapshot_builder import build_snapshot
            from src.shared.websocket_manager import ws_manager
            snapshot = await build_snapshot()
            await ws_manager.broadcast("snapshot", snapshot)

    result = {"status": "ok", "row": body.row, "col": body.col, "type": cell_type.value}
    if station_created:
        result["station"] = station_created
    return result


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
