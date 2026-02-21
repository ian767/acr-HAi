import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.shared.redis import close_redis, get_redis

# Configure logging so handler messages are visible in dev
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await get_redis()

    # Auto-create tables
    from src.shared.base_model import Base
    from src.shared.database import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight schema migration for SQLite dev databases:
        # add columns that were introduced after initial table creation.
        if "sqlite" in settings.database_url:
            import sqlalchemy as sa

            _MIGRATIONS = [
                ("inventory", "sku_name", "VARCHAR(200)"),
                ("inventory", "band", "VARCHAR(1) DEFAULT 'C'"),
                # Robot reservation fields
                ("robots", "reserved", "BOOLEAN DEFAULT 0"),
                ("robots", "reservation_order_id", "VARCHAR(36)"),
                ("robots", "reservation_pick_task_id", "VARCHAR(36)"),
                ("robots", "reservation_station_id", "VARCHAR(36)"),
                # Robot tote possession fields
                ("robots", "hold_pick_task_id", "VARCHAR(36)"),
                ("robots", "hold_at_station", "BOOLEAN DEFAULT 0"),
                # Station queue fields
                ("stations", "approach_cell_row", "INTEGER"),
                ("stations", "approach_cell_col", "INTEGER"),
                ("stations", "holding_cell_row", "INTEGER"),
                ("stations", "holding_cell_col", "INTEGER"),
                ("stations", "queue_cells_json", "TEXT"),
                ("stations", "current_robot_id", "VARCHAR(36)"),
                # Tote barcode denormalization
                ("pick_tasks", "target_tote_barcode", "VARCHAR(100)"),
                ("put_wall_slots", "target_tote_barcode", "VARCHAR(100)"),
            ]
            for table, col, col_type in _MIGRATIONS:
                try:
                    await conn.execute(
                        sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    )
                except Exception:
                    pass  # Column already exists

    # Seed database
    if settings.seed_on_startup:
        from src.shared.database import async_session_factory
        from src.seed import seed_database
        import src.shared.simulation_state as simulation_state

        async with async_session_factory() as session:
            grid = await seed_database(session)
            if grid:
                simulation_state.grid = grid

    # Event bus
    from src.shared.event_bus import event_bus
    from src.handlers import register_all_handlers

    register_all_handlers(event_bus)
    await event_bus.start()

    yield

    # Shutdown
    await event_bus.stop()
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from src.wes.router import router as wes_router
    from src.ess.router import router as ess_router
    from src.wms_adapter.router import router as wms_router
    from src.monitoring.router import router as monitoring_router

    app.include_router(wes_router, prefix="/api/wes", tags=["WES"])
    app.include_router(ess_router, prefix="/api/ess", tags=["ESS"])
    app.include_router(wms_router, prefix="/api/wms", tags=["WMS"])
    app.include_router(monitoring_router, prefix="/api", tags=["Monitoring"])

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        from src.shared.websocket_manager import ws_manager
        from src.shared.snapshot_builder import build_snapshot

        await ws_manager.connect(websocket)
        try:
            snapshot = await build_snapshot()
            await ws_manager.send_snapshot(websocket, snapshot)
            while True:
                await websocket.receive_text()  # keep-alive
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)

    return app


app = create_app()
