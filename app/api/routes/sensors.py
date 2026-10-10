"""Sensor endpoints: CRUD, single/batch reading ingest, anomaly history."""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_analyst, require_engineer, require_viewer
from app.db.database import get_db_session
from app.db.models import AuditLog, DetectorReadings, TrafficAnomalies, TrafficSensors
from app.models.schemas import ReadingIngest, SensorCreate, SensorResponse

router = APIRouter(prefix="/sensors", tags=["Sensors"])


def _sensor_to_dict(s: TrafficSensors) -> dict:
    return {
        "id": str(s.id), "sensor_id": s.sensor_id, "sensor_type": s.sensor_type,
        "name": s.name, "corridor_id": s.corridor_id, "tmc_code": s.tmc_code,
        "direction": s.direction, "milepost": s.milepost, "geometry": s.geometry,
        "is_active": s.is_active,
        "installation_date": s.installation_date.isoformat() if s.installation_date else None,
        "last_reading_at": s.last_reading_at.isoformat() if s.last_reading_at else None,
    }


@router.get("", summary="List sensors")
async def list_sensors(
    sensor_type: Optional[str] = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = select(TrafficSensors).limit(limit)
    if sensor_type:
        q = q.where(TrafficSensors.sensor_type == sensor_type)
    if active_only:
        q = q.where(TrafficSensors.is_active.is_(True))
    rows = (await db.execute(q)).scalars().all()
    return [_sensor_to_dict(r) for r in rows]


@router.post("", summary="Register sensor")
async def create_sensor(
    body: SensorCreate,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_engineer),
) -> dict:
    row = TrafficSensors(
        id=uuid.uuid4(), sensor_id=body.sensor_id,
        sensor_type=body.sensor_type.value if hasattr(body.sensor_type, "value") else str(body.sensor_type),
        name=body.name, corridor_id=body.corridor_id, tmc_code=body.tmc_code,
        direction=body.direction, milepost=body.milepost, geometry=body.geometry,
        is_active=body.is_active, installation_date=datetime.utcnow(),
    )
    db.add(row)
    db.add(AuditLog(actor=_role, action="sensor.create", resource_type="sensor", resource_id=body.sensor_id))
    await db.commit()
    return _sensor_to_dict(row)


@router.post("/{sensor_id}/readings", summary="Ingest single reading")
async def ingest_reading(
    sensor_id: str,
    body: ReadingIngest,
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    row = DetectorReadings(
        id=uuid.uuid4(), sensor_id=sensor_id or body.sensor_id,
        timestamp=body.timestamp, speed_mph=body.speed_mph,
        volume_vph=body.volume_vph, occupancy_pct=body.occupancy_pct,
        headway_sec=body.headway_sec, source=body.source,
        quality_score=body.quality_score,
    )
    db.add(row)
    await db.commit()
    return {"status": "ingested", "id": str(row.id), "sensor_id": row.sensor_id}


@router.post("/readings/batch", summary="Batch ingest (up to 5000 readings)")
async def ingest_batch(
    readings: List[ReadingIngest],
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_analyst),
) -> dict:
    if len(readings) > 5000:
        return {"status": "rejected", "reason": "batch exceeds 5000 readings"}
    for b in readings:
        db.add(DetectorReadings(
            id=uuid.uuid4(), sensor_id=b.sensor_id, timestamp=b.timestamp,
            speed_mph=b.speed_mph, volume_vph=b.volume_vph,
            occupancy_pct=b.occupancy_pct, headway_sec=b.headway_sec,
            source=b.source, quality_score=b.quality_score,
        ))
    await db.commit()
    return {"status": "ingested", "count": len(readings)}


@router.get("/{sensor_id}/anomalies", summary="Sensor anomaly history")
async def sensor_anomalies(
    sensor_id: str,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
    _role: str = Depends(require_viewer),
) -> List[dict]:
    q = (
        select(TrafficAnomalies)
        .where(TrafficAnomalies.sensor_id == sensor_id)
        .order_by(TrafficAnomalies.detected_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(q)).scalars().all()
    return [{"id": str(r.id), "severity": r.severity, "incident_type": r.incident_type,
             "anomaly_score": r.anomaly_score, "status": r.status,
             "detected_at": r.detected_at.isoformat() if r.detected_at else None} for r in rows]
