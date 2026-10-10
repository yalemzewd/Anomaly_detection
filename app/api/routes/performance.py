"""Performance endpoints: corridors, corridor history, PM3 compute/metrics/state
summary, NPMRDS travel times + sync trigger, WZDx summary + active zones."""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_analyst, require_engineer, require_viewer
from app.db.database import get_db_session
from app.db.models import (
    AuditLog,
    CorridorPerformance,
    Corridors,
    PM3ReliabilityMetrics,
    TravelTimeRecords,
    WZDXWorkZones,
)
from app.models.pm3_calculator import compute_lottr, compute_phed, compute_tttr
from app.models.schemas import PM3ComputeRequest, PM3MetricsResponse, StateSummaryResponse

router = APIRouter(prefix="/performance", tags=["Performance"])


@router.get("/corridors", summary="List corridors")
async def list_corridors(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    rows = (await db.execute(select(Corridors))).scalars().all()
    return [{"id": str(c.id), "corridor_id": c.corridor_id, "name": c.name,
             "state": c.state, "tmc_codes": c.tmc_codes or [],
             "length_miles": c.length_miles} for c in rows]


@router.get("/corridors/{corridor_id}/history", summary="Hourly performance history")
async def corridor_history(
    corridor_id: str,
    hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (
        select(CorridorPerformance)
        .where(CorridorPerformance.corridor_id == corridor_id,
               CorridorPerformance.window_start >= since)
        .order_by(CorridorPerformance.window_start.asc())
    )
    rows = (await db.execute(q)).scalars().all()
    return [{"window_start": r.window_start.isoformat() if r.window_start else None,
             "avg_speed_mph": r.avg_speed_mph, "total_volume": r.total_volume,
             "anomaly_count": r.anomaly_count, "incident_count": r.incident_count} for r in rows]


@router.post("/pm3/compute", response_model=PM3MetricsResponse, summary="Compute LOTTR/TTTR/PHED for TMC")
async def pm3_compute(
    body: PM3ComputeRequest,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> PM3MetricsResponse:
    year_start = datetime(body.year, 1, 1)
    year_end = datetime(body.year + 1, 1, 1)
    q = select(TravelTimeRecords).where(
        TravelTimeRecords.tmc_code == body.tmc_code,
        TravelTimeRecords.epoch_start >= year_start,
        TravelTimeRecords.epoch_start < year_end,
    ).limit(50000)
    rows = (await db.execute(q)).scalars().all()
    recs = [{"epoch_start": r.epoch_start, "travel_time_sec": r.travel_time_sec,
             "speed_mph": r.speed_mph, "volume_vph": 0.0} for r in rows]
    lottr = compute_lottr(recs)
    tttr = compute_tttr(recs)
    phed = compute_phed(recs)
    existing = (await db.execute(
        select(PM3ReliabilityMetrics).where(
            PM3ReliabilityMetrics.tmc_code == body.tmc_code,
            PM3ReliabilityMetrics.year == body.year,
        )
    )).scalars().first()
    if existing:
        row = existing
    else:
        row = PM3ReliabilityMetrics(id=uuid.uuid4(), tmc_code=body.tmc_code, year=body.year)
        db.add(row)
    row.lottr_am = lottr.get("am")
    row.lottr_midday = lottr.get("midday")
    row.lottr_pm = lottr.get("pm")
    row.lottr_weekend = lottr.get("weekend")
    row.lottr_overall = lottr.get("overall")
    row.lottr_reliable = lottr.get("reliable")
    row.tttr = tttr.get("tttr")
    row.tttr_reliable = tttr.get("reliable")
    row.phed_hours = phed
    db.add(AuditLog(actor=_role, action="pm3.compute", resource_type="tmc", resource_id=body.tmc_code))
    await db.commit()
    return PM3MetricsResponse(
        tmc_code=body.tmc_code, year=body.year,
        lottr_am=row.lottr_am, lottr_midday=row.lottr_midday, lottr_pm=row.lottr_pm,
        lottr_weekend=row.lottr_weekend, lottr_overall=row.lottr_overall,
        lottr_reliable=row.lottr_reliable, tttr=row.tttr,
        tttr_reliable=row.tttr_reliable, phed_hours=row.phed_hours,
    )


@router.get("/pm3/metrics", summary="List PM3 results")
async def pm3_metrics(
    year: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = select(PM3ReliabilityMetrics).order_by(
        PM3ReliabilityMetrics.year.desc(), PM3ReliabilityMetrics.tmc_code.asc()
    ).limit(limit)
    if year:
        q = q.where(PM3ReliabilityMetrics.year == year)
    rows = (await db.execute(q)).scalars().all()
    return [{"tmc_code": r.tmc_code, "year": r.year, "lottr_overall": r.lottr_overall,
             "lottr_reliable": r.lottr_reliable, "tttr": r.tttr,
             "tttr_reliable": r.tttr_reliable, "phed_hours": r.phed_hours} for r in rows]


@router.get("/pm3/state-summary", response_model=StateSummaryResponse, summary="State-level HPMS biennial summary")
async def pm3_state_summary(
    state: str = Query(..., min_length=2, max_length=2),
    year: int = Query(..., ge=2000, le=2100),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> StateSummaryResponse:
    tmc_rows = (await db.execute(
        select(Corridors.tmc_codes).where(Corridors.state == state.upper())
    )).scalars().all()
    tmcs = sorted({t for lst in (tmc_rows or []) for t in (lst or [])})
    q = select(PM3ReliabilityMetrics).where(
        PM3ReliabilityMetrics.year == year,
        PM3ReliabilityMetrics.tmc_code.in_(tmcs) if tmcs else True,
    )
    rows = (await db.execute(q)).scalars().all()
    total = len(rows)
    rel = sum(1 for r in rows if r.lottr_reliable)
    lots = [r.lottr_overall for r in rows if r.lottr_overall is not None]
    ttts = [r.tttr for r in rows if r.tttr is not None]
    phed = sum(r.phed_hours or 0.0 for r in rows)
    return StateSummaryResponse(
        state=state.upper(), year=year, total_tmcs=total, reliable_tmcs=rel,
        reliability_pct=round(100.0 * rel / total, 2) if total else 0.0,
        avg_lottr=round(sum(lots) / len(lots), 4) if lots else None,
        avg_tttr=round(sum(ttts) / len(ttts), 4) if ttts else None,
        total_phed=round(phed, 2),
    )


@router.get("/npmrds/travel-times", summary="Query NPMRDS travel time records")
async def npmrds_travel_times(
    tmc: str = Query(...),
    hours: int = Query(24, ge=1, le=8760),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (
        select(TravelTimeRecords)
        .where(TravelTimeRecords.tmc_code == tmc, TravelTimeRecords.epoch_start >= since)
        .order_by(TravelTimeRecords.epoch_start.asc())
        .limit(5000)
    )
    rows = (await db.execute(q)).scalars().all()
    return [{"epoch_start": r.epoch_start.isoformat() if r.epoch_start else None,
             "travel_time_sec": r.travel_time_sec, "speed_mph": r.speed_mph,
             "data_density": r.data_density} for r in rows]


@router.post("/npmrds/sync", summary="Trigger NPMRDS sync")
async def npmrds_sync(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    from app.workers.tasks import run_npmrds_sync
    run_npmrds_sync.delay()
    db.add(AuditLog(actor=_role, action="npmrds.sync_trigger", resource_type="npmrds"))
    await db.commit()
    return {"status": "sync_queued", "queued_at": datetime.utcnow().isoformat()}


@router.get("/wzdx/summary", summary="WZDx feed health summary")
async def wzdx_summary(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> dict:
    total = (await db.execute(select(func.count(WZDXWorkZones.id)))).scalar() or 0
    active = (await db.execute(select(func.count(WZDXWorkZones.id)).where(WZDXWorkZones.is_active.is_(True)))).scalar() or 0
    by_state = (await db.execute(select(WZDXWorkZones.state, func.count()).group_by(WZDXWorkZones.state))).all()
    return {"total": total, "active": active, "by_state": {s or "?": c for s, c in by_state}}


@router.get("/wzdx/active", summary="Active work zones")
async def wzdx_active(
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    rows = (await db.execute(select(WZDXWorkZones).where(WZDXWorkZones.is_active.is_(True)).limit(500))).scalars().all()
    return [{"id": str(w.id), "feed_id": w.feed_id, "state": w.state,
             "road_name": w.road_name, "geometry": w.geometry,
             "start_date": w.start_date.isoformat() if w.start_date else None,
             "end_date": w.end_date.isoformat() if w.end_date else None} for w in rows]
