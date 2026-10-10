"""Incident endpoints: CRUD, active list, TIM metrics, clearance recording."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_analyst, require_engineer, require_viewer
from app.db.database import get_db_session
from app.db.models import AuditLog, IncidentEvents
from app.models.schemas import ClearanceUpdate, IncidentCreate, IncidentResponse, TIMMetrics

router = APIRouter(prefix="/incidents", tags=["Incidents"])


def _to_resp(r: IncidentEvents) -> dict:
    return {
        "id": str(r.id), "anomaly_id": str(r.anomaly_id) if r.anomaly_id else None,
        "tmc_code": r.tmc_code, "incident_type": r.incident_type,
        "kabco_severity": r.kabco_severity, "lanes_blocked": r.lanes_blocked,
        "notes": r.notes, "status": r.status,
        "detected_at": r.detected_at.isoformat() if r.detected_at else None,
        "verified_at": r.verified_at.isoformat() if r.verified_at else None,
        "cleared_at": r.cleared_at.isoformat() if r.cleared_at else None,
        "clearance_time_min": r.clearance_time_min, "tim_compliant": r.tim_compliant,
        "rit_itis_id": r.rit_itis_id,
    }


@router.get("", summary="List incidents")
async def list_incidents(
    status: Optional[str] = Query(None),
    incident_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = select(IncidentEvents).order_by(IncidentEvents.detected_at.desc()).limit(limit)
    if status:
        q = q.where(IncidentEvents.status == status)
    if incident_type:
        q = q.where(IncidentEvents.incident_type == incident_type)
    rows = (await db.execute(q)).scalars().all()
    return [_to_resp(r) for r in rows]


@router.post("", summary="Create incident")
async def create_incident(
    body: IncidentCreate,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    import uuid
    row = IncidentEvents(
        id=uuid.uuid4(),
        anomaly_id=body.anomaly_id,
        tmc_code=body.tmc_code,
        incident_type=body.incident_type.value if hasattr(body.incident_type, "value") else str(body.incident_type),
        kabco_severity=body.kabco_severity,
        detected_at=datetime.utcnow(),
        lanes_blocked=body.lanes_blocked,
        status="active",
        notes=body.notes,
    )
    db.add(row)
    db.add(AuditLog(actor=_role, action="incident.create", resource_type="incident", resource_id=str(row.id)))
    await db.commit()
    return _to_resp(row)


@router.get("/active", summary="Real-time active incidents")
async def active_incidents(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = select(IncidentEvents).where(IncidentEvents.status == "active").order_by(IncidentEvents.detected_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return [_to_resp(r) for r in rows]


@router.get("/tim-metrics", response_model=TIMMetrics, summary="TIM NUG 90-min clearance compliance")
async def tim_metrics(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> TIMMetrics:
    total = (await db.execute(select(func.count(IncidentEvents.id)))).scalar() or 0
    ok = (await db.execute(
        select(func.count(IncidentEvents.id)).where(
            IncidentEvents.clearance_time_min.is_not(None),
            IncidentEvents.clearance_time_min <= 90,
        )
    )).scalar() or 0
    avg = (await db.execute(select(func.avg(IncidentEvents.clearance_time_min)))).scalar()
    type_rows = (await db.execute(
        select(IncidentEvents.incident_type, func.count()).group_by(IncidentEvents.incident_type)
    )).all()
    return TIMMetrics(
        total_incidents=total,
        cleared_within_90min=ok,
        compliance_pct=round(100.0 * ok / total, 2) if total else 0.0,
        avg_clearance_min=round(float(avg), 2) if avg is not None else None,
        by_type={t or "unknown": c for t, c in type_rows},
    )


@router.patch("/{incident_id}/clearance", summary="Record clearance + RITIS update")
async def record_clearance(
    incident_id: str,
    body: ClearanceUpdate,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_engineer),
) -> dict:
    row = await db.get(IncidentEvents, incident_id)
    if not row:
        return {"status": "not_found", "id": incident_id}
    row.cleared_at = body.cleared_at
    row.lanes_blocked = body.lanes_blocked
    row.status = "cleared"
    if body.notes:
        row.notes = ((row.notes or "") + "\n" + body.notes).strip()
    det = row.detected_at.replace(tzinfo=None) if row.detected_at and row.detected_at.tzinfo else row.detected_at
    clr = body.cleared_at.replace(tzinfo=None) if body.cleared_at.tzinfo else body.cleared_at
    if det and clr:
        row.clearance_time_min = max(0.0, (clr - det).total_seconds() / 60.0)
        row.tim_compliant = bool(row.clearance_time_min <= 90)
    db.add(AuditLog(actor=_role, action="incident.clear", resource_type="incident", resource_id=incident_id))
    await db.commit()
    # RITIS push is handled by the beat task; record intent here
    return _to_resp(row)
