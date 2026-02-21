"""Dev runner: starts uvicorn with SQLite + fakeredis (no external services needed)."""

import os
import sys

# Set environment BEFORE any src imports
os.environ["ACR_DATABASE_URL"] = "sqlite+aiosqlite:///dev.db"
os.environ["ACR_SEED_ON_STARTUP"] = "true"
os.environ["ACR_WAREHOUSE_CONFIG_PATH"] = os.path.join(
    os.path.dirname(__file__), "..", "config", "warehouse.yaml"
)

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

if __name__ == "__main__":
    import logging
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
