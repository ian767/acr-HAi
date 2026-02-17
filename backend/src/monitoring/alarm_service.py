import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime


class AlarmSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class Alarm:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    severity: AlarmSeverity = AlarmSeverity.INFO
    source: str = ""
    message: str = ""
    acknowledged: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    acknowledged_at: datetime | None = None


class AlarmService:
    """In-memory alarm management service."""

    def __init__(self) -> None:
        self._alarms: list[Alarm] = []

    def raise_alarm(
        self, severity: AlarmSeverity, source: str, message: str
    ) -> Alarm:
        alarm = Alarm(severity=severity, source=source, message=message)
        self._alarms.insert(0, alarm)
        # Keep max 500 alarms in memory
        if len(self._alarms) > 500:
            self._alarms = self._alarms[:500]
        return alarm

    def acknowledge(self, alarm_id: str) -> Alarm | None:
        for alarm in self._alarms:
            if alarm.id == alarm_id:
                alarm.acknowledged = True
                alarm.acknowledged_at = datetime.now()
                return alarm
        return None

    def list_alarms(
        self,
        severity: AlarmSeverity | None = None,
        acknowledged: bool | None = None,
        limit: int = 50,
    ) -> list[Alarm]:
        result = self._alarms
        if severity is not None:
            result = [a for a in result if a.severity == severity]
        if acknowledged is not None:
            result = [a for a in result if a.acknowledged == acknowledged]
        return result[:limit]

    def clear_all(self) -> int:
        count = len(self._alarms)
        self._alarms.clear()
        return count


alarm_service = AlarmService()
