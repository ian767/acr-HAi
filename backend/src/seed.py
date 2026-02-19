"""Database seeding from warehouse.yaml configuration."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.ess.domain.enums import CellType, RobotType
from src.ess.domain.models import Location, Robot, Tote, Zone
from src.wes.domain.models import Inventory, PutWallSlot, Station

logger = logging.getLogger(__name__)


async def seed_database(session: AsyncSession) -> list[list[CellType]] | None:
    """Seed the database from warehouse.yaml.

    Returns the constructed grid if seeding occurred, otherwise ``None``.
    Idempotent: skips seeding if zones already exist.
    """
    # Idempotency check
    result = await session.execute(select(Zone).limit(1))
    if result.scalar_one_or_none() is not None:
        logger.info("Database already seeded, skipping")
        return None

    config_path = Path(settings.warehouse_config_path)
    if not config_path.is_absolute():
        # Resolve relative to project root (two levels up from backend/src)
        config_path = Path(__file__).resolve().parent.parent.parent / config_path
    if not config_path.exists():
        logger.warning("Warehouse config not found at %s, skipping seed", config_path)
        return None

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    zone_cfgs = cfg.get("zones", [])
    if not zone_cfgs:
        logger.warning("No zones defined in warehouse config")
        return None

    zone_cfg = zone_cfgs[0]  # Single-zone for now
    grid_cfg = zone_cfg["grid"]

    # 1. Create Zone
    zone = Zone(
        name=zone_cfg["name"],
        grid_rows=grid_cfg["rows"],
        grid_cols=grid_cfg["cols"],
    )
    session.add(zone)
    await session.flush()
    logger.info("Created zone: %s (%dx%d)", zone.name, grid_cfg["rows"], grid_cfg["cols"])

    # 2. Create Stations + PutWallSlots
    station_map: dict[str, Station] = {}
    for st_cfg in zone_cfg.get("stations", []):
        station = Station(
            name=st_cfg["name"],
            zone_id=zone.id,
            grid_row=st_cfg["position"]["row"],
            grid_col=st_cfg["position"]["col"],
        )
        session.add(station)
        await session.flush()
        station_map[st_cfg["name"]] = station

        for slot_label in st_cfg.get("put_wall_slots", []):
            slot = PutWallSlot(
                station_id=station.id,
                slot_label=slot_label,
            )
            session.add(slot)

    await session.flush()
    logger.info("Created %d stations with put-wall slots", len(station_map))

    # 3. Create Locations
    locations: list[Location] = []

    # Rack locations
    for rack_cfg in zone_cfg.get("racks", []):
        rack_id = rack_cfg["id"]
        rack_row = rack_cfg["position"]["row"]
        rack_col = rack_cfg["position"]["col"]
        floors = rack_cfg.get("floors", 4)
        slots_per_floor = rack_cfg.get("slots_per_floor", 6)

        for floor in range(1, floors + 1):
            for slot in range(1, slots_per_floor + 1):
                loc = Location(
                    label=f"{rack_id}-F{floor}-S{slot:02d}",
                    zone_id=zone.id,
                    rack_id=rack_id,
                    floor=floor,
                    grid_row=rack_row,
                    grid_col=rack_col,
                )
                session.add(loc)
                locations.append(loc)

    # Cantilever locations
    for i, cant_cfg in enumerate(zone_cfg.get("cantilevers", []), 1):
        loc = Location(
            label=f"CANT-{i:02d}",
            zone_id=zone.id,
            grid_row=cant_cfg["position"]["row"],
            grid_col=cant_cfg["position"]["col"],
        )
        session.add(loc)
        locations.append(loc)

    # Station locations
    for st_name, station in station_map.items():
        loc = Location(
            label=f"STN-{st_name}",
            zone_id=zone.id,
            grid_row=station.grid_row,
            grid_col=station.grid_col,
        )
        session.add(loc)
        locations.append(loc)

    await session.flush()
    logger.info("Created %d locations", len(locations))

    # 4. Create Totes on rack locations
    rack_locations = [loc for loc in locations if loc.rack_id is not None]
    sku_counts: dict[str, int] = defaultdict(int)
    tote_count = 0

    for i, loc in enumerate(rack_locations[:60]):
        sku_num = (i % 20) + 1
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
        sku_counts[sku] += qty
        tote_count += 1

    await session.flush()
    logger.info("Created %d totes", tote_count)

    # 5. Create Inventory records
    for sku, total_qty in sku_counts.items():
        inv = Inventory(
            sku=sku,
            zone_id=zone.id,
            total_qty=total_qty,
            allocated_qty=0,
        )
        session.add(inv)

    await session.flush()
    logger.info("Created %d inventory records", len(sku_counts))

    # 6. Create Robots
    robots_cfg = zone_cfg.get("robots", {})
    robot_count = 0

    # K50H robots
    k50h_cfg = robots_cfg.get("k50h", {})
    for i, pos in enumerate(k50h_cfg.get("start_positions", [])[:k50h_cfg.get("count", 0)]):
        robot = Robot(
            name=f"K50H-{i+1:03d}",
            type=RobotType.K50H,
            zone_id=zone.id,
            grid_row=pos["row"],
            grid_col=pos["col"],
            speed=cfg.get("simulation", {}).get("robot_speeds", {}).get("k50h", 1.5),
        )
        session.add(robot)
        robot_count += 1

    # A42TD robots
    a42td_cfg = robots_cfg.get("a42td", {})
    for i, pos in enumerate(a42td_cfg.get("start_positions", [])[:a42td_cfg.get("count", 0)]):
        robot = Robot(
            name=f"A42TD-{i+1:03d}",
            type=RobotType.A42TD,
            zone_id=zone.id,
            grid_row=pos["row"],
            grid_col=pos["col"],
            speed=cfg.get("simulation", {}).get("robot_speeds", {}).get("a42td", 1.0),
        )
        session.add(robot)
        robot_count += 1

    await session.flush()
    logger.info("Created %d robots", robot_count)

    # 7. Build grid
    rows, cols = grid_cfg["rows"], grid_cfg["cols"]
    grid: list[list[CellType]] = [
        [CellType.FLOOR for _ in range(cols)] for _ in range(rows)
    ]

    # Walls
    for wall_cfg in zone_cfg.get("walls", []):
        fr, fc = wall_cfg["from"]["row"], wall_cfg["from"]["col"]
        tr, tc = wall_cfg["to"]["row"], wall_cfg["to"]["col"]
        for r in range(fr, tr + 1):
            for c in range(fc, tc + 1):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = CellType.WALL

    # Racks
    for rack_cfg in zone_cfg.get("racks", []):
        r, c = rack_cfg["position"]["row"], rack_cfg["position"]["col"]
        if 0 <= r < rows and 0 <= c < cols:
            grid[r][c] = CellType.RACK

    # Cantilevers (legacy: treated as RACK cells)
    for cant_cfg in zone_cfg.get("cantilevers", []):
        r, c = cant_cfg["position"]["row"], cant_cfg["position"]["col"]
        if 0 <= r < rows and 0 <= c < cols:
            grid[r][c] = CellType.RACK

    # Stations
    for st_cfg in zone_cfg.get("stations", []):
        r, c = st_cfg["position"]["row"], st_cfg["position"]["col"]
        if 0 <= r < rows and 0 <= c < cols:
            grid[r][c] = CellType.STATION

    await session.commit()
    logger.info("Seeding complete. Grid: %dx%d", rows, cols)
    return grid
