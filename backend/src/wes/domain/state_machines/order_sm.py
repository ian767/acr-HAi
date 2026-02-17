"""Pure-function state machine for Order lifecycle transitions."""

from src.wes.domain.enums import OrderStatus

# Mapping: (current_state, event) -> (next_state, [side_effects])
_TRANSITIONS: dict[
    tuple[OrderStatus, str], tuple[OrderStatus, list[str]]
] = {
    (OrderStatus.NEW, "allocate"): (
        OrderStatus.ALLOCATING,
        ["run_allocation_engine"],
    ),
    (OrderStatus.ALLOCATING, "station_assigned"): (
        OrderStatus.ALLOCATED,
        ["create_pick_tasks", "emit_order_allocated"],
    ),
    (OrderStatus.ALLOCATED, "pick_started"): (
        OrderStatus.IN_PROGRESS,
        ["emit_order_in_progress"],
    ),
    (OrderStatus.IN_PROGRESS, "all_picked"): (
        OrderStatus.COMPLETED,
        ["emit_order_completed"],
    ),
}

# States from which cancellation is allowed (all non-terminal states).
_CANCELLABLE: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.NEW,
        OrderStatus.ALLOCATING,
        OrderStatus.ALLOCATED,
        OrderStatus.IN_PROGRESS,
    }
)


def transition(
    current: OrderStatus, event: str
) -> tuple[OrderStatus, list[str]]:
    """Return (new_state, side_effects) or raise ``ValueError``."""

    # Cancel is a universal escape from any non-terminal state.
    if event == "cancel":
        if current in _CANCELLABLE:
            return OrderStatus.CANCELLED, ["release_inventory", "emit_order_cancelled"]
        raise ValueError(
            f"Cannot cancel order in terminal state {current.value!r}"
        )

    key = (current, event)
    if key not in _TRANSITIONS:
        raise ValueError(
            f"Invalid transition: state={current.value!r}, event={event!r}"
        )

    return _TRANSITIONS[key]
