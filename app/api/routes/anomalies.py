"""Anomaly endpoints: list, active, summary, trigger scan, false-positive, resolve."""

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_analyst, require_viewer
from app.db.database import get_db_session
from app.db.models import AuditLog, TrafficAnomalies
from app.models.schemas import AnomalySummary

router = APIRouter(prefix="/anomalies", tags=["Anomalies"])


@router.get("", summary="List anomalies (filter by severity, type, TMC)")
async def list_anomalies(
    severity: Optional[str] = Query(None),
    incident_type: Optional[str] = Query(None),
    tmc: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="open|resolved|false_positive"),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = select(TrafficAnomalies).order_by(TrafficAnomalies.detected_at.desc()).limit(limit)
    if severity:
        q = q.where(TrafficAnomalies.severity == severity)
    if incident_type:
        q = q.where(TrafficAnomalies.incident_type == incident_type)
    if tmc:
        q = q.where(TrafficAnomalies.tmc_code == tmc)
    if status:
        q = q.where(TrafficAnomalies.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id), "sensor_id": r.sensor_id, "tmc_code": r.tmc_code,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "anomaly_score": r.anomaly_score, "incident_type": r.incident_type,
            "severity": r.severity, "status": r.status,
            "waze_corroborated": r.waze_corroborated,
            "wzdx_suppressed": r.wzdx_suppressed,
        }
        for r in rows
    ]


@router.get("/active", summary="Open anomalies above severity threshold")
async def active_anomalies(
    min_severity: str = Query("medium", description="low|medium|high|critical"),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    floor = order.get(min_severity, 1)
    sev = [k for k, v in order.items() if v >= floor]
    q = (
        select(TrafficAnomalies)
        .where(TrafficAnomalies.status == "open", TrafficAnomalies.severity.in_(sev))
        .order_by(TrafficAnomalies.detected_at.desc())
        .limit(500)
    )
    rows = (await db.execute(q)).scalars().all()
    return [{"id": str(r.id), "sensor_id": r.sensor_id, "severity": r.severity,
             "incident_type": r.incident_type, "anomaly_score": r.anomaly_score,
             "detected_at": r.detected_at.isoformat() if r.detected_at else None} for r in rows]


@router.get("/summary", response_model=AnomalySummary, summary="KPI dashboard")
async def anomaly_summary(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> AnomalySummary:
    total = (await db.execute(select(func.count(TrafficAnomalies.id)))).scalar() or 0
    open_c = (await db.execute(select(func.count(TrafficAnomalies.id)).where(TrafficAnomalies.status == "open"))).scalar() or 0
    res_c = (await db.execute(select(func.count(TrafficAnomalies.id)).where(TrafficAnomalies.status == "resolved"))).scalar() or 0
    fp_c = (await db.execute(select(func.count(TrafficAnomalies.id)).where(TrafficAnomalies.status == "false_positive"))).scalar() or 0
    sev_rows = (await db.execute(select(TrafficAnomalies.severity, func.count()).group_by(TrafficAnomalies.severity))).all()
    type_rows = (await db.execute(select(TrafficAnomalies.incident_type, func.count()).group_by(TrafficAnomalies.incident_type))).all()
    waze_c = (await db.execute(select(func.count(TrafficAnomalies.id)).where(TrafficAnomalies.waze_corroborated.is_(True)))).scalar() or 0
    wzdx_c = (await db.execute(select(func.count(TrafficAnomalies.id)).where(TrafficAnomalies.wzdx_suppressed.is_(True)))).scalar() or 0
    return AnomalySummary(
        total=total, open=open_c, resolved=res_c, false_positives=fp_c,
        by_severity={s or "unknown": c for s, c in sev_rows},
        by_type={t or "unknown": c for t, c in type_rows},
        waze_corroborated=waze_c, wzdx_suppressed=wzdx_c,
    )


@router.post("/trigger-scan", summary="Manual detection scan")
async def trigger_scan(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    from app.workers.tasks import run_anomaly_scan
    run_anomaly_scan.delay()
    db.add(AuditLog(actor=_role, action="anomaly.trigger_scan", resource_type="scan"))
    await db.commit()
    return {"status": "scan_queued", "queued_at": datetime.utcnow().isoformat()}


@router.post("/{anomaly_id}/false-positive", summary="Mark false positive")
async def mark_false_positive(
    anomaly_id: str,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    row = await db.get(TrafficAnomalies, anomaly_id)
    if not row:
        return {"status": "not_found", "id": anomaly_id}
    row.status = "false_positive"
    db.add(AuditLog(actor=_role, action="anomaly.false_positive", resource_type="anomaly", resource_id=anomaly_id))
    await db.commit()
    return {"status": "marked_false_positive", "id": anomaly_id}


@router.post("/{anomaly_id}/resolve", summary="Mark resolved + compute clearance time")
async def resolve_anomaly(
    anomaly_id: str,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    row = await db.get(TrafficAnomalies, anomaly_id)
    if not row:
        return {"status": "not_found", "id": anomaly_id}
    now = datetime.utcnow()
    row.status = "resolved"
    row.resolved_at = now
    if row.detected_at:
        det = row.detected_at.replace(tzinfo=None) if row.detected_at.tzinfo else row.detected_at
        row.clearance_time_min = max(0.0, (now - det).total_seconds() / 60.0)
    db.add(AuditLog(actor=_role, action="anomaly.resolve", resource_type="anomaly", resource_id=anomaly_id))
    await db.commit()
    return {"status": "resolved", "id": anomaly_id, "clearance_time_min": row.clearance_time_min}
