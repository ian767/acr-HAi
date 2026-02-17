"""FastAPI router for the Equipment Scheduling System (ESS)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.deps import SessionDep
from src.ess.application.fleet_manager import FleetManager
from src.ess.application.path_planner import PathPlanner
from src.ess.application.zone_manager import ZoneManager
from src.ess.domain.enums import CellType, RobotStatus
from src.ess.simulation.physics_engine import PhysicsEngine
from src.ess.simulation.presets import SimulationPresets
from src.ess.simulation.robot_simulator import RobotSimulator
from src.ess.infrastructure.redis_cache import RobotStateCache
from src.shared.redis import get_redis
import src.shared.simulation_state as simulation_state

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


class SpeedRequest(BaseModel):
    speed: float


class PresetRequest(BaseModel):
    name: str


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
            raise HTTPException(status_code=404, detail="Zone not found")
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

    await engine.start()
    return {"status": "started"}


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
async def simulation_reset():
    """Stop the engine and reset all simulation state."""
    global _engine, _simulator
    if _engine is not None:
        await _engine.stop()
    _engine = None
    _simulator = None
    return {"status": "reset"}


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
    }


@router.post("/simulation/presets/apply")
async def simulation_apply_preset(body: PresetRequest, session: SessionDep):
    """Apply a simulation preset (creates zone, robots, etc.)."""
    try:
        preset = SimulationPresets.get_preset(body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    zm = ZoneManager(session)
    zone_cfg = preset["zone"]
    zone = await zm.create_zone(zone_cfg["name"], zone_cfg["rows"], zone_cfg["cols"])

    # Build grid config from preset.
    grid_config: dict[str, list[list[int]]] = {
        "walls": [],
        "racks": [],
        "cantilevers": [],
        "stations": [],
        "aisles": [],
        "charging": [],
    }

    rack_cfg = preset.get("racks", {})
    for r in rack_cfg.get("rows", []):
        for c in rack_cfg.get("cols", []):
            grid_config["racks"].append([r, c])

    cant_cfg = preset.get("cantilevers", {})
    cant_row = cant_cfg.get("row")
    if cant_row is not None:
        for c in cant_cfg.get("cols", []):
            grid_config["cantilevers"].append([cant_row, c])

    for st in preset.get("stations", []):
        grid_config["stations"].append([st["row"], st["col"]])

    simulation_state.grid = await zm.build_grid(zone.id, grid_config)

    # Register robots.
    fm = FleetManager(session)
    from src.ess.domain.enums import RobotType

    robot_cfg = preset.get("robots", {})
    a42td_count = robot_cfg.get("a42td_count", 0)
    k50h_count = robot_cfg.get("k50h_count", 0)

    for i in range(a42td_count):
        await fm.register_robot(
            name=f"A42TD-{i+1:03d}",
            type=RobotType.A42TD,
            zone_id=zone.id,
            row=0,
            col=i,
        )
    for i in range(k50h_count):
        await fm.register_robot(
            name=f"K50H-{i+1:03d}",
            type=RobotType.K50H,
            zone_id=zone.id,
            row=1,
            col=i,
        )

    # Apply speed from preset.
    engine = _get_engine()
    engine.set_speed(preset.get("speed", 1.0))

    await session.commit()

    return {
        "status": "applied",
        "preset": body.name,
        "zone_id": str(zone.id),
        "robots_created": a42td_count + k50h_count,
        "totes": preset.get("totes", 0),
    }
