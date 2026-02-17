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
