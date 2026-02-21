"""Canned simulation presets for quick demos and testing."""

from __future__ import annotations


def _build_queue_cells(station_row: int, station_col: int, queue_size: int = 3) -> dict:
    """Generate approach, holding, and queue cell positions for a station.

    Layout (going upward from station):
    station(row, col) -> approach(row-1, col) -> Q1(row-2, col) -> Q2(row-3, col) -> holding(row-4, col)
    """
    approach_row = station_row - 1
    queue_cells = []
    for i in range(queue_size):
        queue_cells.append({
            "position": i,
            "row": station_row - 2 - i,
            "col": station_col,
        })
    holding_row = station_row - 2 - queue_size

    return {
        "approach_cell": {"row": approach_row, "col": station_col},
        "holding_cell": {"row": holding_row, "col": station_col},
        "queue_cells": queue_cells,
    }


_PRESETS: dict[str, dict] = {
    "interactive": {
        "description": "Minimal setup for manual operation: 1 K50H, 1 A42TD, 1 station",
        "zone": {"name": "interactive", "rows": 15, "cols": 20},
        "robots": {
            "a42td_count": 1,
            "k50h_count": 1,
        },
        "totes": 10,
        "racks": {
            # Groups of 2 with aisle rows between: [2,3] aisle@4 [5,6] edge@7
            "rows": [2, 3, 5, 6],
            "cols": range(2, 8),
        },
        "rack_edge_row": 7,  # FLOOR aisle row = cantilever handoff
        "stations": [
            {"row": 13, "col": 5, **_build_queue_cells(13, 5, queue_size=2)},
        ],
        "tick_interval_ms": 150,
        "speed": 1.0,
        "wes_driven": True,
        "auto_dispatch": False,
        "orders_per_minute": 0,
        "station_processing_ticks": 0,
        "sku_count": 5,
        "totes_per_rack_slot": 1,
        "floors_per_rack": 10,
        "interactive_mode": True,
    },
    "demo_small": {
        "description": "Small WES demo with 5 robots and 20 totes",
        "zone": {"name": "demo", "rows": 20, "cols": 30},
        "robots": {
            "a42td_count": 3,
            "k50h_count": 2,
        },
        "totes": 20,
        "racks": {
            # Groups of 2 with aisles: [2,3] @4 [5,6] @7 [8,9] edge@10
            "rows": [2, 3, 5, 6, 8, 9],
            "cols": range(2, 12),
        },
        "rack_edge_row": 10,  # FLOOR aisle row = cantilever handoff
        "stations": [
            {"row": 18, "col": 5, **_build_queue_cells(18, 5, queue_size=3)},
            {"row": 18, "col": 10, **_build_queue_cells(18, 10, queue_size=3)},
        ],
        "tick_interval_ms": 150,
        "speed": 1.0,
        # WES-driven simulation
        "wes_driven": True,
        "auto_dispatch": False,
        "orders_per_minute": 4.0,
        "station_processing_ticks": 5,
        "sku_count": 10,
        "totes_per_rack_slot": 1,
        "floors_per_rack": 10,
    },
    "demo_medium": {
        "description": "Medium WES demo with 20 robots and 100 totes",
        "zone": {"name": "medium", "rows": 40, "cols": 60},
        "robots": {
            "a42td_count": 12,
            "k50h_count": 8,
        },
        "totes": 100,
        "racks": {
            # Groups of 2 with aisles: [2,3]@4 [5,6]@7 [8,9]@10 [11,12]@13 [14,15]@16 [17,18] edge@19
            "rows": [2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18],
            "cols": range(2, 28),
        },
        "rack_edge_row": 19,  # FLOOR aisle row = cantilever handoff
        "stations": [
            {"row": 35, "col": 10, **_build_queue_cells(35, 10, queue_size=3)},
            {"row": 35, "col": 20, **_build_queue_cells(35, 20, queue_size=3)},
            {"row": 35, "col": 30, **_build_queue_cells(35, 30, queue_size=3)},
            {"row": 35, "col": 40, **_build_queue_cells(35, 40, queue_size=3)},
        ],
        "tick_interval_ms": 150,
        "speed": 1.0,
        # WES-driven simulation
        "wes_driven": True,
        "auto_dispatch": False,
        "orders_per_minute": 6.0,
        "station_processing_ticks": 5,
        "sku_count": 20,
        "totes_per_rack_slot": 1,
        "floors_per_rack": 10,
    },
    "stress_test": {
        "description": "Stress test with 100 robots and 500 totes",
        "zone": {"name": "stress", "rows": 80, "cols": 120},
        "robots": {
            "a42td_count": 60,
            "k50h_count": 40,
        },
        "totes": 500,
        "racks": {
            # Groups of 2 with aisles: 10 groups from row 2
            "rows": [r for g in range(10) for r in (2 + g * 3, 3 + g * 3)],
            "cols": range(2, 50),
        },
        "rack_edge_row": 32,  # FLOOR aisle row after last group [29,30]
        "stations": [
            {"row": 70, "col": c, **_build_queue_cells(70, c, queue_size=4)}
            for c in range(10, 110, 10)
        ],
        "tick_interval_ms": 100,
        "speed": 2.0,
    },
    "stress_bottleneck": {
        "description": "Narrow corridor bottleneck: 25×40, 20 robots, wall with 2-cell gap",
        "zone": {"name": "bottleneck", "rows": 25, "cols": 40},
        "robots": {"a42td_count": 0, "k50h_count": 20},
        "totes": 0,
        "walls": [
            [r, c]
            for r in range(10, 13)
            for c in range(1, 39)
            if c not in (19, 20)
        ],
        "racks": {"rows": [], "cols": []},
        "stations": [],
        "tick_interval_ms": 100,
        "speed": 1.0,
        "auto_dispatch": True,
    },
    "stress_crosstraffic": {
        "description": "Cross traffic: 30×30, 30 robots, 4 rack blocks + central cross aisle",
        "zone": {"name": "crosstraffic", "rows": 30, "cols": 30},
        "robots": {"a42td_count": 0, "k50h_count": 30},
        "totes": 0,
        "racks": {},
        "rack_blocks": [
            {"r0": 2, "c0": 2, "size": 7},
            {"r0": 2, "c0": 21, "size": 7},
            {"r0": 21, "c0": 2, "size": 7},
            {"r0": 21, "c0": 21, "size": 7},
        ],
        "stations": [],
        "tick_interval_ms": 100,
        "speed": 1.0,
        "auto_dispatch": True,
    },
    "stress_dense": {
        "description": "High density: 40×60, 50 robots, rack rows + aisles",
        "zone": {"name": "dense", "rows": 40, "cols": 60},
        "robots": {"a42td_count": 0, "k50h_count": 50},
        "totes": 0,
        "racks": {},
        "dense_racks": {
            "start_row": 3,
            "end_row": 37,
            "row_step": 4,
            "rack_depth": 2,
            "col_start": 2,
            "col_end": 58,
            "vertical_aisle_every": 8,
        },
        "stations": [],
        "tick_interval_ms": 80,
        "speed": 1.0,
        "auto_dispatch": True,
    },
}


class SimulationPresets:
    """Registry of pre-configured simulation scenarios."""

    @staticmethod
    def get_preset(name: str) -> dict:
        """Return the preset configuration by name.

        Raises :class:`KeyError` if the preset does not exist.
        """
        if name not in _PRESETS:
            raise KeyError(
                f"Unknown preset '{name}'. Available: {list(_PRESETS.keys())}"
            )
        return _PRESETS[name]

    @staticmethod
    def list_presets() -> list[str]:
        """Return the names of all available presets."""
        return list(_PRESETS.keys())
