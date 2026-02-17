"""Canned simulation presets for quick demos and testing."""

from __future__ import annotations


_PRESETS: dict[str, dict] = {
    "demo_small": {
        "description": "Small demo with 5 robots and 20 totes",
        "zone": {"name": "demo", "rows": 20, "cols": 30},
        "robots": {
            "a42td_count": 3,
            "k50h_count": 2,
        },
        "totes": 20,
        "racks": {
            "rows": range(2, 8),
            "cols": range(2, 12),
        },
        "cantilevers": {
            "row": 9,
            "cols": range(2, 12),
        },
        "stations": [
            {"row": 18, "col": 5},
            {"row": 18, "col": 10},
        ],
        "tick_interval_ms": 150,
        "speed": 1.0,
    },
    "demo_medium": {
        "description": "Medium demo with 20 robots and 100 totes",
        "zone": {"name": "medium", "rows": 40, "cols": 60},
        "robots": {
            "a42td_count": 12,
            "k50h_count": 8,
        },
        "totes": 100,
        "racks": {
            "rows": range(2, 16),
            "cols": range(2, 28),
        },
        "cantilevers": {
            "row": 17,
            "cols": range(2, 28),
        },
        "stations": [
            {"row": 35, "col": 10},
            {"row": 35, "col": 20},
            {"row": 35, "col": 30},
            {"row": 35, "col": 40},
        ],
        "tick_interval_ms": 150,
        "speed": 1.0,
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
            "rows": range(2, 30),
            "cols": range(2, 50),
        },
        "cantilevers": {
            "row": 31,
            "cols": range(2, 50),
        },
        "stations": [
            {"row": 70, "col": c}
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
        "cantilevers": {},
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
        "cantilevers": {},
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
        "cantilevers": {},
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
