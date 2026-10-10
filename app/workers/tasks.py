"""Celery tasks + beat schedule for FHWA-AGENT-003.

Beat schedule per README:
- run_anomaly_scan    60s    Full ML detection scan (TSMO)
- run_alert_dispatch  30s    Multi-channel alert delivery
- run_ritis_sync      2min   TIM coordination pull + push
- run_waze_poll       90s    Waze CCP jam refresh
- run_wzdx_poll       5min   WZDx work zone feed sync
- run_corridor_perf   60min  Hourly corridor aggregation
- run_npmrds_sync     60min  NPMRDS 15-min epoch ingestion
- run_pm3_compute     daily 02:00 ET  23 CFR 490 reliability metrics
- purge_old_readings  weekly Sun 03:00 ET  Retain 90 days
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from celery import Celery, shared_task
from celery.schedules import crontab

logger = logging.getLogger(__name__)

celery_app = Celery("fhwa_agent3")
celery_app.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="US/Eastern",
    beat_schedule={
        "anomaly-scan-60s": {"task": "app.workers.tasks.run_anomaly_scan", "schedule": 60.0},
        "alert-dispatch-30s": {"task": "app.workers.tasks.run_alert_dispatch", "schedule": 30.0},
        "ritis-sync-2min": {"task": "app.workers.tasks.run_ritis_sync", "schedule": 120.0},
        "waze-poll-90s": {"task": "app.workers.tasks.run_waze_poll", "schedule": 90.0},
        "wzdx-poll-5min": {"task": "app.workers.tasks.run_wzdx_poll", "schedule": 300.0},
        "corridor-perf-60min": {"task": "app.workers.tasks.run_corridor_perf", "schedule": 3600.0},
        "npmrds-sync-60min": {"task": "app.workers.tasks.run_npmrds_sync", "schedule": 3600.0},
        "pm3-compute-daily": {
            "task": "app.workers.tasks.run_pm3_compute",
            "schedule": crontab(hour=2, minute=0),
        },
        "purge-readings-weekly": {
            "task": "app.workers.tasks.purge_old_readings",
            "schedule": crontab(hour=3, minute=0, day_of_week="sun"),
        },
    },
)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


@shared_task(bind=True, queue="realtime", name="app.workers.tasks.run_anomaly_scan")
def run_anomaly_scan(self) -> dict:
    """60s TSMO scan: latest reading per active sensor -> ensemble -> persist."""
    async def _scan() -> dict:
        from sqlalchemy import select
        from app.db.database import AsyncSessionLocal
        from app.db.models import DetectorReadings, TrafficAnomalies, TrafficSensors, WazeJamRecords, WZDXWorkZones
        from app.services.corroboration import corroborate_anomaly, near_active_work_zone
        from app.services.detection_pipeline import run_detection
        scanned, flagged = 0, 0
        async with AsyncSessionLocal() as db:
            sensors = (await db.execute(
                select(TrafficSensors).where(TrafficSensors.is_active.is_(True)).limit(2000)
            )).scalars().all()
            jams = (await db.execute(
                select(WazeJamRecords).where(
                    WazeJamRecords.published_at >= datetime.utcnow() - timedelta(minutes=30)
                ).limit(2000)
            )).scalars().all()
            jam_dicts = [{"latitude": j.latitude, "longitude": j.longitude,
                          "published_at": j.published_at, "speed_mph": j.speed_mph} for j in jams]
            zones = (await db.execute(
                select(WZDXWorkZones).where(WZDXWorkZones.is_active.is_(True)).limit(1000)
            )).scalars().all()
            zone_dicts = [{"is_active": True, "geometry": z.geometry} for z in zones]
            for s in sensors:
                rows = (await db.execute(
                    select(DetectorReadings)
                    .where(DetectorReadings.sensor_id == s.sensor_id)
                    .order_by(DetectorReadings.timestamp.desc()).limit(24)
                )).scalars().all()
                if not rows:
                    continue
                scanned += 1
                latest = rows[0]
                window = [{"speed_mph": r.speed_mph or 0.0, "volume_vph": r.volume_vph or 0.0,
                           "occupancy_pct": r.occupancy_pct or 0.0, "headway_sec": r.headway_sec or 0.0}
                          for r in reversed(rows)]
                current = {"speed_mph": latest.speed_mph or 0.0, "volume_vph": latest.volume_vph or 0.0,
                           "occupancy_pct": latest.occupancy_pct or 0.0, "headway_sec": latest.headway_sec or 0.0}
                lat = lon = None
                if isinstance(s.geometry, dict):
                    c = s.geometry.get("coordinates") or []
                    if len(c) >= 2:
                        lon, lat = float(c[0]), float(c[1])
                waze = corroborate_anomaly(lat, lon, jam_dicts) if lat is not None else {"waze_corroborated": False}
                wz = near_active_work_zone(lat, lon, zone_dicts) if lat is not None else {"near_work_zone": False}
                res = run_detection(s.sensor_id, window, current,
                                    waze_corroborated=waze["waze_corroborated"],
                                    near_work_zone=wz["near_work_zone"], tmc_code=s.tmc_code)
                if res["is_anomalous"]:
                    flagged += 1
                    det_at = datetime.fromisoformat(res["detected_at"]) if isinstance(res["detected_at"], str) else res["detected_at"]
                    db.add(TrafficAnomalies(
                        id=uuid.uuid4(), sensor_id=s.sensor_id, tmc_code=s.tmc_code,
                        detected_at=det_at, anomaly_score=res["anomaly_score"],
                        lstm_score=res["lstm_score"], isoforest_score=res["isoforest_score"],
                        incident_type=res["incident_type"], severity=res["severity"],
                        status="open", waze_corroborated=res["waze_corroborated"],
                        wzdx_suppressed=res["wzdx_suppressed"],
                    ))
            await db.commit()
        return {"status": "completed", "scanned": scanned, "flagged": flagged,
                "at": datetime.utcnow().isoformat()}
    try:
        return _run(_scan())
    except Exception as e:
        logger.exception("anomaly scan failed")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, queue="realtime", name="app.workers.tasks.run_alert_dispatch")
def run_alert_dispatch(self) -> dict:
    """30s multi-channel alert delivery for unalerted HIGH/CRITICAL anomalies."""
    async def _dispatch() -> dict:
        from sqlalchemy import select
        from app.db.database import AsyncSessionLocal
        from app.db.models import AnomalyAlerts, TrafficAnomalies
        from app.services.alert_dispatcher import dispatch_anomaly
        sent = 0
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(TrafficAnomalies)
                .where(TrafficAnomalies.status == "open",
                       TrafficAnomalies.severity.in_(["high", "critical"])).limit(100)
            )).scalars().all()
            for r in rows:
                already = (await db.execute(
                    select(AnomalyAlerts).where(AnomalyAlerts.anomaly_id == r.id).limit(1)
                )).scalars().first()
                if already:
                    continue
                anomaly = {"sensor_id": r.sensor_id, "tmc_code": r.tmc_code,
                           "severity": r.severity, "incident_type": r.incident_type,
                           "anomaly_score": r.anomaly_score or 0.0,
                           "waze_corroborated": r.waze_corroborated}
                results = await dispatch_anomaly(anomaly)
                for channel, ok in results.items():
                    if ok:
                        db.add(AnomalyAlerts(id=uuid.uuid4(), anomaly_id=r.id,
                                             channel=channel, status="sent",
                                             payload={"severity": r.severity}))
                        sent += 1
            await db.commit()
        return {"status": "completed", "alerts_sent": sent}
    try:
        return _run(_dispatch())
    except Exception as e:
        logger.exception("alert dispatch failed")
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True, queue="integrations", name="app.workers.tasks.run_ritis_sync")
def run_ritis_sync(self) -> dict:
    """2min RITIS pull active events + push new incidents."""
    async def _sync() -> dict:
        from sqlalchemy import select
        from app.db.database import AsyncSessionLocal
        from app.db.models import IncidentEvents, RITISEvents
        from app.services.integrations import RITISClient
        client = RITISClient()
        pulled = await client.pull_active_events()
        pushed = 0
        async with AsyncSessionLocal() as db:
            for ev in pulled:
                db.add(RITISEvents(id=uuid.uuid4(), ritis_event_id=str(ev.get("id", "")),
                                   direction="pull", event_type=str(ev.get("type", "")),
                                   payload=ev, status="synced"))
            new_inc = (await db.execute(
                select(IncidentEvents).where(IncidentEvents.status == "active",
                                             IncidentEvents.rit_itis_id.is_(None)).limit(50)
            )).scalars().all()
            for inc in new_inc:
                res = await client.push_incident({
                    "type": inc.incident_type, "tmc": inc.tmc_code,
                    "detected_at": inc.detected_at.isoformat() if inc.detected_at else None,
                })
                if res.get("status") == "ok":
                    pushed += 1
                    body = res.get("body") or {}
                    inc.rit_itis_id = str(body.get("id", body.get("event_id", "")) or "")
                    db.add(RITISEvents(id=uuid.uuid4(), direction="push",
                                       event_type=inc.incident_type, status="synced"))
            await db.commit()
        return {"status": "completed", "pulled": len(pulled), "pushed": pushed}
    try:
        return _run(_sync())
    except Exception as e:
        logger.exception("ritis sync failed")
        raise self.retry(exc=e, countdown=120)


@shared_task(bind=True, queue="integrations", name="app.workers.tasks.run_waze_poll")
def run_waze_poll(self) -> dict:
    """90s Waze CCP jam refresh."""
    async def _poll() -> dict:
        import uuid as _uuid
        from app.db.database import AsyncSessionLocal
        from app.db.models import WazeJamRecords
        from app.services.integrations import WazeClient
        jams = await WazeClient().poll_jams()
        async with AsyncSessionLocal() as db:
            for j in jams[:2000]:
                try:
                    pub = j.get("published_at") or j.get("pubTime")
                    pub_dt = datetime.fromisoformat(pub) if isinstance(pub, str) else datetime.utcnow()
                except ValueError:
                    pub_dt = datetime.utcnow()
                db.add(WazeJamRecords(
                    id=_uuid.uuid4(), jam_id=str(j.get("id", j.get("jam_id", ""))),
                    latitude=float(j.get("latitude", j.get("lat", 0.0))),
                    longitude=float(j.get("longitude", j.get("lon", 0.0))),
                    speed_mph=j.get("speed_mph", j.get("speed")),
                    delay_sec=j.get("delay_sec", j.get("delay")),
                    jam_level=j.get("jam_level", j.get("level")),
                    published_at=pub_dt,
                ))
            await db.commit()
        return {"status": "completed", "jams": len(jams)}
    try:
        return _run(_poll())
    except Exception as e:
        logger.exception("waze poll failed")
        raise self.retry(exc=e, countdown=90)


@shared_task(bind=True, queue="integrations", name="app.workers.tasks.run_wzdx_poll")
def run_wzdx_poll(self) -> dict:
    """5min WZDx work zone feed sync."""
    async def _poll() -> dict:
        import uuid as _uuid
        from datetime import datetime as _dt
        from app.db.database import AsyncSessionLocal
        from app.db.models import WZDXWorkZones
        from app.services.integrations import WZDXClient
        zones = await WZDXClient().sync_feeds()
        async with AsyncSessionLocal() as db:
            for z in zones:
                db.add(WZDXWorkZones(
                    id=_uuid.uuid4(), feed_id=z.get("feed_id", ""),
                    state=z.get("state", ""), road_name=z.get("road_name"),
                    geometry=z.get("geometry"), is_active=True,
                ))
            await db.commit()
        return {"status": "completed", "zones": len(zones)}
    try:
        return _run(_poll())
    except Exception as e:
        logger.exception("wzdx poll failed")
        raise self.retry(exc=e, countdown=300)


@shared_task(bind=True, queue="analytics", name="app.workers.tasks.run_corridor_perf")
def run_corridor_perf(self) -> dict:
    """Hourly corridor performance aggregation."""
    async def _agg() -> dict:
        import uuid as _uuid
        from sqlalchemy import func, select
        from app.db.database import AsyncSessionLocal
        from app.db.models import CorridorPerformance, Corridors, DetectorReadings, TrafficSensors
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=1)
        done = 0
        async with AsyncSessionLocal() as db:
            corridors = (await db.execute(select(Corridors).limit(500))).scalars().all()
            for c in corridors:
                sids = (await db.execute(
                    select(TrafficSensors.sensor_id).where(TrafficSensors.corridor_id == c.corridor_id)
                )).scalars().all()
                if not sids:
                    continue
                agg = (await db.execute(
                    select(func.avg(DetectorReadings.speed_mph), func.sum(DetectorReadings.volume_vph))
                    .where(DetectorReadings.sensor_id.in_(sids),
                           DetectorReadings.timestamp >= start, DetectorReadings.timestamp < now)
                )).first()
                db.add(CorridorPerformance(
                    id=_uuid.uuid4(), corridor_id=c.corridor_id, window_start=start,
                    avg_speed_mph=float(agg[0]) if agg and agg[0] else None,
                    total_volume=float(agg[1]) if agg and agg[1] else 0.0,
                ))
                done += 1
            await db.commit()
        return {"status": "completed", "corridors": done, "window": start.isoformat()}
    try:
        return _run(_agg())
    except Exception as e:
        logger.exception("corridor perf failed")
        raise self.retry(exc=e, countdown=600)


@shared_task(bind=True, queue="analytics", name="app.workers.tasks.run_npmrds_sync")
def run_npmrds_sync(self) -> dict:
    """Hourly NPMRDS 15-min epoch ingestion."""
    async def _sync() -> dict:
        import uuid as _uuid
        from sqlalchemy import select
        from app.db.database import AsyncSessionLocal
        from app.db.models import Corridors, TravelTimeRecords
        from app.services.integrations import NPMRDSClient
        end = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=1)
        client = NPMRDSClient()
        total = 0
        async with AsyncSessionLocal() as db:
            tmc_lists = (await db.execute(select(Corridors.tmc_codes).limit(500))).scalars().all()
            tmcs = sorted({t for lst in (tmc_lists or []) for t in (lst or [])})[:500]
            if tmcs:
                recs = await client.fetch_travel_times(tmcs, start, end)
                for r in recs[:10000]:
                    try:
                        ep = r.get("epoch_start") or r.get("epoch")
                        ep_dt = datetime.fromisoformat(ep) if isinstance(ep, str) else start
                    except ValueError:
                        ep_dt = start
                    db.add(TravelTimeRecords(
                        id=_uuid.uuid4(), tmc_code=r.get("tmc_code", r.get("tmc", "")),
                        epoch_start=ep_dt,
                        travel_time_sec=float(r.get("travel_time_sec", r.get("travelTime", 0.0))),
                        speed_mph=r.get("speed_mph", r.get("speed")),
                        data_density=r.get("data_density", r.get("density")),
                    ))
                    total += 1
            await db.commit()
        return {"status": "completed", "records": total}
    try:
        return _run(_sync())
    except Exception as e:
        logger.exception("npmrds sync failed")
        raise self.retry(exc=e, countdown=600)


@shared_task(bind=True, queue="analytics", name="app.workers.tasks.run_pm3_compute")
def run_pm3_compute(self) -> dict:
    """Daily 02:00 ET 23 CFR 490 LOTTR/TTTR/PHED computation for prior year."""
    async def _compute() -> dict:
        import uuid as _uuid
        from sqlalchemy import select
        from app.db.database import AsyncSessionLocal
        from app.db.models import PM3ReliabilityMetrics, TravelTimeRecords
        from app.models.pm3_calculator import compute_lottr, compute_phed, compute_tttr
        year = datetime.utcnow().year - 1
        y0, y1 = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        async with AsyncSessionLocal() as db:
            tmcs = (await db.execute(
                select(TravelTimeRecords.tmc_code).where(
                    TravelTimeRecords.epoch_start >= y0, TravelTimeRecords.epoch_start < y1
                ).distinct().limit(5000)
            )).scalars().all()
            done = 0
            for tmc in tmcs:
                rows = (await db.execute(
                    select(TravelTimeRecords).where(
                        TravelTimeRecords.tmc_code == tmc,
                        TravelTimeRecords.epoch_start >= y0,
                        TravelTimeRecords.epoch_start < y1,
                    ).limit(50000)
                )).scalars().all()
                recs = [{"epoch_start": r.epoch_start, "travel_time_sec": r.travel_time_sec,
                         "speed_mph": r.speed_mph, "volume_vph": 0.0} for r in rows]
                lottr = compute_lottr(recs)
                tttr = compute_tttr(recs)
                phed = compute_phed(recs)
                ex = (await db.execute(
                    select(PM3ReliabilityMetrics).where(
                        PM3ReliabilityMetrics.tmc_code == tmc,
                        PM3ReliabilityMetrics.year == year)
                )).scalars().first()
                row = ex or PM3ReliabilityMetrics(id=_uuid.uuid4(), tmc_code=tmc, year=year)
                if ex is None:
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
                done += 1
            await db.commit()
        return {"status": "completed", "year": year, "tmcs": done}
    try:
        return _run(_compute())
    except Exception as e:
        logger.exception("pm3 compute failed")
        raise self.retry(exc=e, countdown=3600)


@shared_task(bind=True, queue="analytics", name="app.workers.tasks.purge_old_readings")
def purge_old_readings(self) -> dict:
    """Weekly Sunday 03:00 ET purge: retain 90 days of detector readings."""
    async def _purge() -> dict:
        from sqlalchemy import delete
        from app.db.database import AsyncSessionLocal
        from app.db.models import DetectorReadings, WazeJamRecords
        cutoff = datetime.utcnow() - timedelta(days=90)
        async with AsyncSessionLocal() as db:
            r1 = await db.execute(delete(DetectorReadings).where(DetectorReadings.timestamp < cutoff))
            r2 = await db.execute(delete(WazeJamRecords).where(WazeJamRecords.published_at < cutoff))
            await db.commit()
            return {"status": "completed",
                    "readings_deleted": r1.rowcount, "jams_deleted": r2.rowcount}
    try:
        return _run(_purge())
    except Exception as e:
        logger.exception("purge failed")
        raise self.retry(exc=e, countdown=3600)
