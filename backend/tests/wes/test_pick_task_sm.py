"""Unit tests for the PickTask state machine (pure functions, no I/O)."""

import pytest

from src.wes.domain.enums import PickTaskState
from src.wes.domain.state_machines.pick_task_sm import transition


# ---------------------------------------------------------------------------
# Happy-path: walk the entire lifecycle
# ---------------------------------------------------------------------------


class TestPickTaskSmHappyPath:
    """Verify every valid transition in sequence."""

    def test_source_requested_to_source_at_cantilever(self):
        new_state, effects = transition(
            PickTaskState.SOURCE_REQUESTED, "source_at_cantilever"
        )
        assert new_state == PickTaskState.SOURCE_AT_CANTILEVER
        assert isinstance(effects, list)
        assert "notify_k50h_ready" in effects

    def test_source_at_cantilever_to_source_at_station(self):
        new_state, effects = transition(
            PickTaskState.SOURCE_AT_CANTILEVER, "source_at_station"
        )
        assert new_state == PickTaskState.SOURCE_AT_STATION
        assert "activate_station_display" in effects

    def test_source_at_station_to_picking(self):
        new_state, effects = transition(
            PickTaskState.SOURCE_AT_STATION, "scan_started"
        )
        assert new_state == PickTaskState.PICKING
        assert "start_pick_timer" in effects

    def test_picking_to_return_requested(self):
        new_state, effects = transition(
            PickTaskState.PICKING, "pick_complete"
        )
        assert new_state == PickTaskState.RETURN_REQUESTED
        assert "request_tote_return" in effects

    def test_return_requested_to_return_at_cantilever(self):
        new_state, effects = transition(
            PickTaskState.RETURN_REQUESTED, "return_at_cantilever"
        )
        assert new_state == PickTaskState.RETURN_AT_CANTILEVER
        assert "notify_a42td_return" in effects

    def test_return_at_cantilever_to_completed(self):
        new_state, effects = transition(
            PickTaskState.RETURN_AT_CANTILEVER, "source_back_in_rack"
        )
        assert new_state == PickTaskState.COMPLETED
        assert "emit_pick_task_completed" in effects

    def test_full_lifecycle(self):
        """Walk through the entire lifecycle from SOURCE_REQUESTED to COMPLETED."""
        state = PickTaskState.SOURCE_REQUESTED

        state, _ = transition(state, "source_at_cantilever")
        assert state == PickTaskState.SOURCE_AT_CANTILEVER

        state, _ = transition(state, "source_at_station")
        assert state == PickTaskState.SOURCE_AT_STATION

        state, _ = transition(state, "scan_started")
        assert state == PickTaskState.PICKING

        state, _ = transition(state, "pick_complete")
        assert state == PickTaskState.RETURN_REQUESTED

        state, _ = transition(state, "return_at_cantilever")
        assert state == PickTaskState.RETURN_AT_CANTILEVER

        state, _ = transition(state, "source_back_in_rack")
        assert state == PickTaskState.COMPLETED


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestPickTaskSmInvalidTransitions:
    """Verify that invalid transitions raise ValueError."""

    @pytest.mark.parametrize(
        "state,event",
        [
            # Cannot skip forward
            (PickTaskState.SOURCE_REQUESTED, "source_at_station"),
            (PickTaskState.SOURCE_REQUESTED, "scan_started"),
            (PickTaskState.SOURCE_REQUESTED, "pick_complete"),
            (PickTaskState.SOURCE_REQUESTED, "return_at_cantilever"),
            (PickTaskState.SOURCE_REQUESTED, "source_back_in_rack"),
            # Cannot go backward
            (PickTaskState.PICKING, "source_at_cantilever"),
            (PickTaskState.RETURN_REQUESTED, "scan_started"),
            (PickTaskState.COMPLETED, "source_at_cantilever"),
            # Wrong event for each state
            (PickTaskState.SOURCE_AT_CANTILEVER, "scan_started"),
            (PickTaskState.SOURCE_AT_STATION, "pick_complete"),
            (PickTaskState.PICKING, "source_at_station"),
            (PickTaskState.RETURN_REQUESTED, "source_back_in_rack"),
            (PickTaskState.RETURN_AT_CANTILEVER, "pick_complete"),
            # Completely unknown event
            (PickTaskState.SOURCE_REQUESTED, "nonexistent_event"),
            (PickTaskState.PICKING, ""),
        ],
    )
    def test_invalid_transition_raises(self, state: PickTaskState, event: str):
        with pytest.raises(ValueError, match="Invalid transition"):
            transition(state, event)

    def test_completed_state_rejects_all_events(self):
        """No transitions are allowed from the terminal COMPLETED state."""
        events = [
            "source_at_cantilever",
            "source_at_station",
            "scan_started",
            "pick_complete",
            "return_at_cantilever",
            "source_back_in_rack",
        ]
        for event in events:
            with pytest.raises(ValueError, match="Invalid transition"):
                transition(PickTaskState.COMPLETED, event)


# ---------------------------------------------------------------------------
# Return-value structure
# ---------------------------------------------------------------------------


class TestPickTaskSmReturnStructure:
    """Verify the shape of the return value."""

    def test_returns_tuple(self):
        result = transition(PickTaskState.SOURCE_REQUESTED, "source_at_cantilever")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_first_element_is_state(self):
        new_state, _ = transition(
            PickTaskState.SOURCE_REQUESTED, "source_at_cantilever"
        )
        assert isinstance(new_state, PickTaskState)

    def test_second_element_is_list_of_strings(self):
        _, effects = transition(
            PickTaskState.SOURCE_REQUESTED, "source_at_cantilever"
        )
        assert isinstance(effects, list)
        assert all(isinstance(e, str) for e in effects)
