"""Pure-function state machine for PickTask lifecycle transitions."""

from src.wes.domain.enums import PickTaskState

# Mapping: (current_state, event) -> (next_state, [side_effects])
_TRANSITIONS: dict[
    tuple[PickTaskState, str], tuple[PickTaskState, list[str]]
] = {
    (PickTaskState.CREATED, "reserve"): (
        PickTaskState.RESERVED,
        ["create_reservation"],
    ),
    (PickTaskState.RESERVED, "request_source"): (
        PickTaskState.SOURCE_REQUESTED,
        ["dispatch_retrieve_flow"],
    ),
    (PickTaskState.SOURCE_REQUESTED, "source_at_cantilever"): (
        PickTaskState.SOURCE_AT_CANTILEVER,
        ["notify_k50h_ready"],
    ),
    (PickTaskState.SOURCE_AT_CANTILEVER, "source_picked"): (
        PickTaskState.SOURCE_PICKED,
        ["notify_k50h_departing"],
    ),
    (PickTaskState.SOURCE_PICKED, "source_at_station"): (
        PickTaskState.SOURCE_AT_STATION,
        ["activate_station_display"],
    ),
    # Existing return flow
    (PickTaskState.SOURCE_AT_STATION, "scan_started"): (
        PickTaskState.PICKING,
        ["start_pick_timer"],
    ),
    (PickTaskState.PICKING, "pick_complete"): (
        PickTaskState.RETURN_REQUESTED,
        ["request_tote_return"],
    ),
    (PickTaskState.RETURN_REQUESTED, "return_at_cantilever"): (
        PickTaskState.RETURN_AT_CANTILEVER,
        ["notify_a42td_return"],
    ),
    (PickTaskState.RETURN_AT_CANTILEVER, "source_back_in_rack"): (
        PickTaskState.COMPLETED,
        ["emit_pick_task_completed"],
    ),
    # Complete at station (CV-1 route)
    (PickTaskState.SOURCE_AT_STATION, "complete"): (
        PickTaskState.COMPLETED,
        ["emit_pick_task_completed"],
    ),
}


def transition(
    current: PickTaskState, event: str
) -> tuple[PickTaskState, list[str]]:
    """Return (new_state, side_effects) or raise ``ValueError``."""

    key = (current, event)
    if key not in _TRANSITIONS:
        raise ValueError(
            f"Invalid transition: state={current.value!r}, event={event!r}"
        )

    return _TRANSITIONS[key]
