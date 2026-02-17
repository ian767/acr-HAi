from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "ACR_"}

    # Application
    app_name: str = "ACR-Hai"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/acr_hai"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Simulation
    tick_interval_ms: int = 150
    simulation_speed: float = 1.0

    # WebSocket
    ws_throttle_ms: int = 100
    ws_backpressure_limit: int = 65536  # 64KB

    # Seeding
    seed_on_startup: bool = False
    warehouse_config_path: str = "config/warehouse.yaml"


settings = Settings()
