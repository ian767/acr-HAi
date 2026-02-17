from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from src.monitoring.alarm_service import AlarmSeverity, alarm_service
from src.monitoring.metrics_service import metrics_service

router = APIRouter()


@router.get("/alarms")
async def list_alarms(
    severity: AlarmSeverity | None = Query(None),
    acknowledged: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    alarms = alarm_service.list_alarms(
        severity=severity, acknowledged=acknowledged, limit=limit
    )
    return [asdict(a) for a in alarms]


@router.post("/alarms/{alarm_id}/ack")
async def acknowledge_alarm(alarm_id: str):
    alarm = alarm_service.acknowledge(alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return asdict(alarm)


@router.get("/metrics")
async def get_metrics():
    snapshot = metrics_service.get_snapshot()
    return asdict(snapshot)
