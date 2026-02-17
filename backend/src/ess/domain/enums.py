import enum


class RobotType(str, enum.Enum):
    K50H = "K50H"      # Small picker: cantilever <-> station
    A42TD = "A42TD"     # Large carrier: rack <-> cantilever


class RobotStatus(str, enum.Enum):
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    MOVING = "MOVING"
    WAITING = "WAITING"
    DOCKING = "DOCKING"
    BLOCKED = "BLOCKED"
    CHARGING = "CHARGING"


class CellType(str, enum.Enum):
    FLOOR = "FLOOR"
    RACK = "RACK"
    CANTILEVER = "CANTILEVER"
    STATION = "STATION"
    AISLE = "AISLE"
    WALL = "WALL"
    CHARGING = "CHARGING"


class ToteStatus(str, enum.Enum):
    STORED = "STORED"
    IN_TRANSIT = "IN_TRANSIT"
    AT_STATION = "AT_STATION"
    RETURNING = "RETURNING"


class EquipmentTaskType(str, enum.Enum):
    RETRIEVE = "RETRIEVE"
    RETURN = "RETURN"


class EquipmentTaskState(str, enum.Enum):
    PENDING = "PENDING"
    A42TD_MOVING = "A42TD_MOVING"
    AT_CANTILEVER = "AT_CANTILEVER"
    K50H_MOVING = "K50H_MOVING"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
