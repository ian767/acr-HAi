"""E2E test: Seed -> Order -> Allocate -> Simulation -> WebSocket.

Runs against SQLite (in-memory) + fakeredis so no external services needed.
"""

from __future__ import annotations

import asyncio
import os
import sys

# ---- Patch BEFORE any src imports ----
os.environ["ACR_DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["ACR_SEED_ON_STARTUP"] = "true"
os.environ["ACR_WAREHOUSE_CONFIG_PATH"] = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "warehouse.yaml"
)

# Patch database module to use SQLite-compatible engine with StaticPool
# (in-memory SQLite needs StaticPool so all connections share the same DB)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_sqlite_engine = create_async_engine(
    "sqlite+aiosqlite://",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_sqlite_session_factory = async_sessionmaker(
    _sqlite_engine, class_=AsyncSession, expire_on_commit=False
)

import src.shared.database as db_mod
db_mod.engine = _sqlite_engine
db_mod.async_session_factory = _sqlite_session_factory

async def _get_session():
    async with _sqlite_session_factory() as session:
        yield session

db_mod.get_session = _get_session

# Patch redis to use fakeredis
import fakeredis.aioredis
import src.shared.redis as redis_mod

_fake_redis = None

async def _get_fake_redis():
    global _fake_redis
    if _fake_redis is None:
        _fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return _fake_redis

async def _close_fake_redis():
    global _fake_redis
    if _fake_redis is not None:
        await _fake_redis.aclose()
        _fake_redis = None

redis_mod.get_redis = _get_fake_redis
redis_mod.close_redis = _close_fake_redis

# Now import app modules
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.shared.base_model import Base
from src.seed import seed_database
from src.shared.event_bus import event_bus
from src.handlers import register_all_handlers
import src.shared.simulation_state as simulation_state


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, condition, detail))


async def setup():
    """Manually run what lifespan would do (httpx doesn't trigger lifespan)."""
    # Create tables
    async with _sqlite_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed
    async with _sqlite_session_factory() as session:
        grid = await seed_database(session)
        if grid:
            simulation_state.grid = grid

    # Start event bus
    register_all_handlers(event_bus)
    await event_bus.start()


async def teardown():
    await event_bus.stop()
    await _close_fake_redis()


async def main():
    print("=" * 60)
    print("ACR-Hai E2E Test")
    print("=" * 60)

    await setup()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        # ---------------------------------------------------------------
        # 0. Health check
        # ---------------------------------------------------------------
        print("\n--- Health Check ---")
        r = await client.get("/api/health")
        check("Health endpoint", r.status_code == 200, f"status={r.status_code}")

        # ---------------------------------------------------------------
        # 1. Seed verification
        # ---------------------------------------------------------------
        print("\n--- Phase 1: Seed Verification ---")

        r = await client.get("/api/ess/zones")
        zones = r.json()
        check("Zones created", len(zones) == 1, f"count={len(zones)}")
        zone_id = zones[0]["id"] if zones else None
        check("Zone name", zones[0]["name"] == "Zone A" if zones else False)

        r = await client.get("/api/wes/stations")
        stations = r.json()
        check("Stations created", len(stations) == 3, f"count={len(stations)}")

        r = await client.get("/api/ess/robots")
        robots = r.json()
        check("Robots created", len(robots) == 14, f"count={len(robots)}")
        k50h_count = sum(1 for ro in robots if ro["type"] == "K50H")
        a42td_count = sum(1 for ro in robots if ro["type"] == "A42TD")
        check("K50H robots", k50h_count == 6, f"count={k50h_count}")
        check("A42TD robots", a42td_count == 8, f"count={a42td_count}")

        r = await client.get("/api/wes/inventory")
        inventory = r.json()
        check("Inventory records", len(inventory) == 20, f"count={len(inventory)}")

        r = await client.get("/api/ess/grid")
        grid_data = r.json()
        check("Grid built", grid_data["rows"] == 30 and grid_data["cols"] == 50,
              f"rows={grid_data['rows']} cols={grid_data['cols']}")

        # Verify grid cell types
        cells = grid_data["cells"]
        flat = [c for row in cells for c in row]
        wall_count = flat.count("WALL")
        rack_count = flat.count("RACK")
        station_count = flat.count("STATION")
        cant_count = flat.count("CANTILEVER")
        check("Grid has walls", wall_count > 0, f"count={wall_count}")
        check("Grid has racks", rack_count == 6, f"count={rack_count}")
        check("Grid has stations", station_count == 3, f"count={station_count}")
        check("Grid has cantilevers", cant_count == 3, f"count={cant_count}")

        # ---------------------------------------------------------------
        # 2. Order Creation (WMS -> WES)
        # ---------------------------------------------------------------
        print("\n--- Phase 2: Order Creation ---")

        r = await client.post("/api/wms/orders", json={
            "external_id": "WMS-E2E-001",
            "sku": "SKU-001",
            "quantity": 3,
            "priority": 5,
            "zone_id": zone_id,
        })
        check("Create order", r.status_code == 200, f"status={r.status_code}")
        order_data = r.json()
        order_id = order_data.get("order_id")
        check("Order ID returned", order_id is not None)
        check("Order status NEW", order_data.get("status") == "NEW")

        r = await client.get("/api/wes/orders")
        orders = r.json()
        check("Order in list", len(orders) >= 1, f"count={len(orders)}")

        # Let event bus process OrderCreated -> WS broadcast
        await asyncio.sleep(0.3)

        # ---------------------------------------------------------------
        # 3. Order Allocation
        # ---------------------------------------------------------------
        print("\n--- Phase 3: Order Allocation ---")

        r = await client.post(f"/api/wes/orders/{order_id}/allocate")
        check("Allocate order", r.status_code == 200, f"status={r.status_code}")
        alloc_data = r.json()
        check("Order status ALLOCATED", alloc_data.get("status") == "ALLOCATED",
              f"status={alloc_data.get('status')}")
        station_id = alloc_data.get("station_id")
        check("Station assigned", station_id is not None)

        # Let event bus process: OrderAllocated -> PickTask + RetrieveSourceTote -> TaskExecutor
        await asyncio.sleep(1.5)

        # ---------------------------------------------------------------
        # 4. PickTask Auto-Created by event handler
        # ---------------------------------------------------------------
        print("\n--- Phase 4: PickTask Verification ---")

        r = await client.get("/api/wes/pick-tasks")
        pick_tasks = r.json()
        check("PickTask created", len(pick_tasks) >= 1, f"count={len(pick_tasks)}")

        if pick_tasks:
            pt = pick_tasks[0]
            check("PickTask SKU matches", pt["sku"] == "SKU-001")
            check("PickTask qty", pt["qty_to_pick"] == 3)
            check("PickTask station", pt["station_id"] == station_id,
                  f"expected={station_id} got={pt['station_id']}")
            check("PickTask state SOURCE_REQUESTED or later",
                  pt["state"] in [
                      "SOURCE_REQUESTED", "SOURCE_AT_CANTILEVER",
                      "SOURCE_AT_STATION",
                  ],
                  f"state={pt['state']}")
            pick_task_id = pt["id"]
        else:
            pick_task_id = None

        # ---------------------------------------------------------------
        # 5. Simulation Start + Run
        # ---------------------------------------------------------------
        print("\n--- Phase 5: Simulation ---")

        r = await client.post("/api/ess/simulation/start")
        check("Simulation started", r.status_code == 200)
        sim_data = r.json()
        check("Simulation status", sim_data.get("status") == "started",
              f"status={sim_data.get('status')}")

        r = await client.get("/api/ess/simulation/config")
        config = r.json()
        check("Engine running", config.get("running") is True)

        # Let simulation run a few ticks
        await asyncio.sleep(0.5)

        r = await client.post("/api/ess/simulation/pause")
        check("Simulation paused", r.status_code == 200)

        # ---------------------------------------------------------------
        # 6. Snapshot Builder
        # ---------------------------------------------------------------
        print("\n--- Phase 6: Snapshot ---")

        from src.shared.snapshot_builder import build_snapshot
        snapshot = await build_snapshot()
        check("Snapshot has robots", len(snapshot.get("robots", {})) == 14,
              f"count={len(snapshot.get('robots', {}))}")
        check("Snapshot has stations", len(snapshot.get("stations", [])) == 3,
              f"count={len(snapshot.get('stations', []))}")
        check("Snapshot has orders", len(snapshot.get("orders", [])) >= 1,
              f"count={len(snapshot.get('orders', []))}")
        check("Snapshot has pick_tasks", len(snapshot.get("pick_tasks", [])) >= 1,
              f"count={len(snapshot.get('pick_tasks', []))}")

        # ---------------------------------------------------------------
        # 7. WMS Status Query
        # ---------------------------------------------------------------
        print("\n--- Phase 7: WMS Status Query ---")

        r = await client.get("/api/wms/order-status/WMS-E2E-001")
        check("WMS status query", r.status_code == 200)
        status_data = r.json()
        check("External ID matches", status_data.get("external_id") == "WMS-E2E-001")
        check("Status is ALLOCATED+",
              status_data.get("status") in ["ALLOCATED", "IN_PROGRESS"],
              f"status={status_data.get('status')}")

        # ---------------------------------------------------------------
        # 8. Order Cancel flow
        # ---------------------------------------------------------------
        print("\n--- Phase 8: Order Cancel ---")

        r = await client.post("/api/wms/orders", json={
            "external_id": "WMS-E2E-002",
            "sku": "SKU-005",
            "quantity": 2,
            "priority": 1,
            "zone_id": zone_id,
        })
        check("Create 2nd order", r.status_code == 200)

        r = await client.post("/api/wms/orders/cancel", json={
            "external_id": "WMS-E2E-002",
        })
        check("Cancel order", r.status_code == 200)
        cancel_data = r.json()
        check("Cancelled status", cancel_data.get("status") == "CANCELLED")

        # ---------------------------------------------------------------
        # 9. Cleanup
        # ---------------------------------------------------------------
        print("\n--- Phase 9: Cleanup ---")

        r = await client.post("/api/ess/simulation/reset")
        check("Simulation reset", r.status_code == 200)

    await teardown()

    # -------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------
    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if failed:
        print("\nFailed checks:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")

    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
