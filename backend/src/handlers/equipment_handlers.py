"""Equipment coordination event handlers (RetrieveSourceTote, ReturnSourceTote)."""

from __future__ import annotations

import logging

from src.handler_support import (
    HandlerServices,
    find_nearest_rack_edge,
    get_robot_position,
    handler_session,
    plan_and_store_path,
    safe_handler,
)
from src.shared.event_bus import EventBus

logger = logging.getLogger(__name__)


async def _advance_waiting_robot(session, station_id) -> None:
    """After a robot leaves the station, clear approach/station slots.

    FIFO pull (_pull_advance_queues) handles advancing the next robot
    from Q1→A on the next tick.  Do NOT call advance_queue() or reroute
    here — those bypass the single-lane pull chain.
    """
    from src.wes.domain.models import Station
    from src.wes.application.station_queue_service import StationQueueService

    station = await session.get(Station, station_id)
    if station is None:
        return

    qsvc = StationQueueService(session)
    qs = qsvc._get_queue_state(station)

    # Clear station + approach slots (robot has left)
    if qs.get("station"):
        qs["station"] = None
        station.current_robot_id = None
    if qs.get("approach"):
        qs["approach"] = None
    qsvc._save_queue_state(station, qs, reason="station_release")
    await session.flush()
    logger.info("Station %s: cleared approach/station slots (FIFO pull will advance)", station.name)


def register(bus: EventBus) -> None:
    from src.wes.domain.events import RetrieveSourceTote, ReturnSourceTote

    bus.subscribe(RetrieveSourceTote, _handle_retrieve_source_tote)
    bus.subscribe(ReturnSourceTote, _handle_return_source_tote)


@safe_handler
async def _handle_retrieve_source_tote(event) -> None:
    """RetrieveSourceTote -> TaskExecutor.execute_retrieve() -> A* path -> Redis."""
    logger.info(
        "RetrieveSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    async with handler_session() as session:
        from src.ess.domain.models import Location
        import src.shared.simulation_state as simulation_state

        svc = HandlerServices(session)
        eq_task = await svc.executor.execute_retrieve(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            source_location_id=event.source_location_id,
            station_id=event.station_id,
        )

        # Track tote origin for heatmap (before path planning).
        try:
            _src_loc = await session.get(Location, event.source_location_id)
            if _src_loc:
                from src.ess.application.tote_origin_tracker import get_tracker
                get_tracker().record_allocated(
                    str(eq_task.id), _src_loc.grid_row, _src_loc.grid_col,
                )
        except Exception:
            pass  # Non-critical tracking

        if eq_task.a42td_robot_id is not None and simulation_state.grid:
            robot = await svc.fm.get_robot(eq_task.a42td_robot_id)
            # Use Redis position (simulation state) if available.
            pos = await get_robot_position(robot.id)
            start = pos or (robot.grid_row, robot.grid_col)

            # Plan A42TD path directly to the rack-edge (cantilever handoff
            # point) near the SOURCE column, not the robot's current
            # position.  This distributes robots across different rack-edge
            # cells and avoids congestion at a single target cell.
            source_loc = await session.get(Location, event.source_location_id)
            ref_row = source_loc.grid_row if source_loc else start[0]
            ref_col = source_loc.grid_col if source_loc else start[1]

            # Extract territory for A42TD pathfinding constraints.
            from src.ess.domain.enums import RobotType as _RobotType
            _a42_tcols = None
            _a42_trows = None
            if robot.territory_col_min is not None:
                _a42_tcols = (robot.territory_col_min, robot.territory_col_max)
            if getattr(robot, "territory_row_min", None) is not None:
                _a42_trows = (robot.territory_row_min, robot.territory_row_max)

            # Collect rack-edge cells already targeted by other active A42TDs.
            from src.ess.infrastructure.redis_cache import RobotStateCache
            from src.shared.redis import get_redis
            from src.ess.domain.models import EquipmentTask as _ET
            from src.ess.domain.enums import EquipmentTaskState as _ETS, EquipmentTaskType as _ETT
            from sqlalchemy import select as _sel
            _active = await session.execute(
                _sel(_ET).where(
                    _ET.type == _ETT.RETRIEVE,
                    _ET.a42td_robot_id.isnot(None),
                    _ET.state.in_([_ETS.PENDING, _ETS.A42TD_MOVING]),
                    _ET.id != eq_task.id,
                )
            )
            avoid: set[tuple[int, int]] = set()
            for other in _active.scalars().all():
                if other.source_location_id:
                    other_loc = await session.get(Location, other.source_location_id)
                    if other_loc:
                        other_re = find_nearest_rack_edge(
                            simulation_state.grid, other_loc.grid_row, other_loc.grid_col,
                        )
                        if other_re:
                            avoid.add(other_re)
                # Also avoid rack-edge cells near A42TDs currently in aisle rows
                if other.a42td_robot_id:
                    other_pos = await get_robot_position(other.a42td_robot_id)
                    if other_pos and other_pos[0] in simulation_state.aisle_rows:
                        aisle_re = find_nearest_rack_edge(
                            simulation_state.grid, other_pos[0], other_pos[1],
                        )
                        if aisle_re:
                            avoid.add(aisle_re)

            # Per-aisle rack-edge: A42TD hands off on its own territory row,
            # not the global rack_edge_row.  This keeps each A42TD in its
            # aisle and avoids bottlenecks at the global cantilever row.
            rack_edge = find_nearest_rack_edge(
                simulation_state.grid, ref_row, ref_col,
                avoid_cells=avoid, territory_rows=_a42_trows,
            )
            # If all nearby cells are taken, fall back without avoid.
            if rack_edge is None:
                rack_edge = find_nearest_rack_edge(
                    simulation_state.grid, ref_row, ref_col,
                    territory_rows=_a42_trows,
                )

            planned = None
            if rack_edge is not None:
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    rack_edge,
                    robot_type=_RobotType.A42TD,
                    territory_cols=_a42_tcols,
                    territory_rows=_a42_trows,
                )
            elif source_loc is not None:
                # Fallback: use the source location directly (legacy path).
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    (source_loc.grid_row, source_loc.grid_col),
                    robot_type=_RobotType.A42TD,
                    territory_cols=_a42_tcols,
                    territory_rows=_a42_trows,
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

            # If A42TD is already at the target rack-edge, fire arrival
            # immediately — the simulation tick won't detect it.
            target = rack_edge or (source_loc.grid_row, source_loc.grid_col) if source_loc else None
            if not planned and target and start == target:
                from src.shared.event_bus import event_bus as _eb
                from src.ess.domain.events import SourceAtCantilever
                from src.wes.domain.models import PickTask as _PT
                _pt = await session.get(_PT, event.pick_task_id)
                if _pt and _pt.source_tote_id:
                    await _eb.publish(SourceAtCantilever(
                        pick_task_id=event.pick_task_id,
                        tote_id=_pt.source_tote_id,
                    ))
                logger.info(
                    "A42TD %s already at rack-edge %s — instant arrival",
                    robot.id, target,
                )

        await session.commit()


@safe_handler
async def _handle_return_source_tote(event) -> None:
    """ReturnSourceTote -> release K50H from station -> execute_return -> path -> Redis."""
    logger.info(
        "ReturnSourceTote: pick_task=%s tote=%s",
        event.pick_task_id, event.tote_id,
    )

    async with handler_session() as session:
        from src.ess.domain.models import Location, Robot
        from src.ess.domain.enums import RobotStatus
        from src.wes.domain.models import Station
        from src.wes.application.reservation_service import ReservationService
        from src.wes.application.station_queue_service import StationQueueService
        from src.ess.infrastructure.redis_cache import RobotStateCache
        from src.shared.redis import get_redis
        from sqlalchemy import select
        import src.shared.simulation_state as simulation_state

        # ── Idempotency guard (MUST be before release logic) ──
        # If a RETURN task already exists, a prior invocation already released
        # the serving K50H.  Skip entirely to avoid releasing the NEXT robot
        # that was promoted into the station slot by advance_queue.
        from src.ess.domain.models import EquipmentTask as _ET_guard
        from src.ess.domain.enums import EquipmentTaskType as _ETT_guard
        _existing_guard = await session.execute(
            select(_ET_guard).where(
                _ET_guard.pick_task_id == event.pick_task_id,
                _ET_guard.type == _ETT_guard.RETURN,
            ).limit(1)
        )
        if _existing_guard.scalar_one_or_none() is not None:
            logger.info(
                "RETURN task already exists for pick_task %s — skipping ReturnSourceTote entirely",
                event.pick_task_id,
            )
            await session.commit()
            return

        # --- Release the K50H that's holding the tote at the station ---
        # It's stuck in WAITING_FOR_STATION; we need to free it for the return trip.
        result = await session.execute(
            select(Robot).where(
                Robot.hold_pick_task_id == event.pick_task_id,
                Robot.hold_at_station == True,  # noqa: E712
            ).limit(1)
        )
        k50h_at_station = result.scalar_one_or_none()

        if k50h_at_station is not None:
            # Clear station-hold state so find_nearest_idle can pick it up
            k50h_at_station.hold_at_station = False
            k50h_at_station.reserved = False
            k50h_at_station.reservation_order_id = None
            k50h_at_station.reservation_pick_task_id = None
            k50h_at_station.reservation_station_id = None
            k50h_at_station.status = RobotStatus.IDLE
            await session.flush()

            # Update Redis to match
            try:
                redis_client = await get_redis()
                cache = RobotStateCache(redis_client)
                await cache.update_status(k50h_at_station.id, RobotStatus.IDLE.value)
                await cache.clear_reservation(k50h_at_station.id)
                await cache.update_tote_possession(
                    k50h_at_station.id,
                    hold_pick_task_id=event.pick_task_id,
                    hold_at_station=False,
                )
            except Exception:
                logger.debug("Redis sync failed during K50H release (non-critical)")

            # Release station queue slot
            qsvc = StationQueueService(session)
            await qsvc.release_station(event.station_id, k50h_at_station.id)
            await session.flush()

            # FIFO queue advance: re-route any K50H waiting in queue
            # now that the station is free.
            await _advance_waiting_robot(session, event.station_id)

            logger.info(
                "Released K50H %s from WAITING_FOR_STATION for return flow",
                k50h_at_station.id,
            )
        else:
            # K50H not found with hold_at_station=True (simulator may have
            # cleared it already).  Only null out current_robot_id — do NOT
            # call release_station() which would clear_robot_from_all_queues
            # on whoever was just promoted into the station slot by
            # advance_queue (the "queue dissolve" bug).
            station = await session.get(Station, event.station_id)
            if station is not None and station.current_robot_id is not None:
                # Clear only the station-level pointer; queue slots are
                # managed by advance_queue and should not be touched.
                qsvc = StationQueueService(session)
                qs = qsvc._get_queue_state(station)
                if qs.get("station"):
                    qs["station"] = None
                    qsvc._save_queue_state(station, qs, reason="return_stale_cleanup")
                else:
                    station.current_robot_id = None
                await session.flush()
                await _advance_waiting_robot(session, event.station_id)
                logger.info(
                    "Cleaned up stale station.current_robot_id at station %s",
                    event.station_id,
                )

        svc = HandlerServices(session)
        eq_task = await svc.executor.execute_return(
            pick_task_id=event.pick_task_id,
            tote_id=event.tote_id,
            target_location_id=event.target_location_id,
            station_id=event.station_id,
        )

        if eq_task.k50h_robot_id is not None and simulation_state.grid:
            robot = await svc.fm.get_robot(eq_task.k50h_robot_id)
            # Use Redis position (simulation state) if available.
            pos = await get_robot_position(robot.id)
            start = pos or (robot.grid_row, robot.grid_col)

            # K50H goes from station to rack-edge (handoff point, not deep rack).
            cant = find_nearest_rack_edge(simulation_state.grid, start[0], start[1])
            planned = None
            if cant is not None:
                planned = await plan_and_store_path(
                    svc, robot.id,
                    start,
                    cant,
                )

            await svc.executor.advance_task(eq_task.id, "a42td_dispatched")

            # If K50H is already at rack-edge, fire ReturnAtCantilever
            # immediately — the simulation tick won't detect it.
            if not planned and cant and start == cant:
                from src.shared.event_bus import event_bus as _eb
                from src.ess.domain.events import ReturnAtCantilever
                await _eb.publish(ReturnAtCantilever(
                    pick_task_id=event.pick_task_id,
                    tote_id=event.tote_id,
                ))
                logger.info(
                    "K50H %s already at rack-edge %s — instant return arrival",
                    robot.id, cant,
                )

        await session.commit()
