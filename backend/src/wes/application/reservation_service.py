"""Robot reservation management for pick task assignments."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ess.domain.models import Robot

logger = logging.getLogger(__name__)


class ReservationService:
    """Manages robot reservations linking robots to orders/pick tasks/stations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_reservation(
        self,
        robot_id: uuid.UUID,
        order_id: uuid.UUID,
        pick_task_id: uuid.UUID,
        station_id: uuid.UUID,
    ) -> Robot:
        robot = await self._session.get(Robot, robot_id)
        if robot is None:
            raise ValueError(f"Robot {robot_id} not found")

        robot.reserved = True
        robot.reservation_order_id = order_id
        robot.reservation_pick_task_id = pick_task_id
        robot.reservation_station_id = station_id
        await self._session.flush()

        # Sync to Redis
        await self._sync_reservation_to_redis(robot)

        logger.info(
            "Reservation created: robot=%s order=%s pick_task=%s station=%s",
            robot_id, order_id, pick_task_id, station_id,
        )
        return robot

    async def clear_reservation(self, robot_id: uuid.UUID) -> Robot:
        robot = await self._session.get(Robot, robot_id)
        if robot is None:
            raise ValueError(f"Robot {robot_id} not found")

        robot.reserved = False
        robot.reservation_order_id = None
        robot.reservation_pick_task_id = None
        robot.reservation_station_id = None
        robot.hold_pick_task_id = None
        robot.hold_at_station = False
        await self._session.flush()

        # Sync to Redis
        await self._clear_reservation_in_redis(robot_id)

        logger.info("Reservation cleared: robot=%s", robot_id)
        return robot

    async def find_reserved_robot_at_station(
        self, station_id: uuid.UUID
    ) -> Robot | None:
        result = await self._session.execute(
            select(Robot).where(
                Robot.hold_at_station == True,  # noqa: E712
                Robot.reservation_station_id == station_id,
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def set_tote_possession(
        self,
        robot_id: uuid.UUID,
        pick_task_id: uuid.UUID,
        at_station: bool = False,
    ) -> Robot:
        robot = await self._session.get(Robot, robot_id)
        if robot is None:
            raise ValueError(f"Robot {robot_id} not found")

        robot.hold_pick_task_id = pick_task_id
        robot.hold_at_station = at_station
        await self._session.flush()

        # Sync to Redis
        await self._sync_tote_to_redis(robot)

        return robot

    async def _sync_reservation_to_redis(self, robot: Robot) -> None:
        try:
            from src.ess.infrastructure.redis_cache import RobotStateCache
            from src.shared.redis import get_redis

            redis_client = await get_redis()
            cache = RobotStateCache(redis_client)
            await cache.update_reservation(
                robot.id,
                reserved=robot.reserved,
                order_id=robot.reservation_order_id,
                pick_task_id=robot.reservation_pick_task_id,
                station_id=robot.reservation_station_id,
            )
        except Exception:
            logger.debug("Redis reservation sync failed (non-critical)")

    async def _clear_reservation_in_redis(self, robot_id: uuid.UUID) -> None:
        try:
            from src.ess.infrastructure.redis_cache import RobotStateCache
            from src.shared.redis import get_redis

            redis_client = await get_redis()
            cache = RobotStateCache(redis_client)
            await cache.clear_reservation(robot_id)
        except Exception:
            logger.debug("Redis reservation clear failed (non-critical)")

    async def _sync_tote_to_redis(self, robot: Robot) -> None:
        try:
            from src.ess.infrastructure.redis_cache import RobotStateCache
            from src.shared.redis import get_redis

            redis_client = await get_redis()
            cache = RobotStateCache(redis_client)
            await cache.update_tote_possession(
                robot.id,
                hold_pick_task_id=robot.hold_pick_task_id,
                hold_at_station=robot.hold_at_station,
            )
        except Exception:
            logger.debug("Redis tote sync failed (non-critical)")
