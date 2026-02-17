"""Shared simulation state accessible from event handlers and routers."""

from __future__ import annotations

from src.ess.application.traffic_controller import TrafficController
from src.ess.domain.enums import CellType

grid: list[list[CellType]] | None = None
traffic: TrafficController = TrafficController()
