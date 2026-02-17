from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.shared.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await get_redis()

    # Auto-create tables
    from src.shared.base_model import Base
    from src.shared.database import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
