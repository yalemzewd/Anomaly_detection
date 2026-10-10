"""End-to-end tests for FHWA-AGENT-003 (Traffic Anomaly Detection).

Runs the full stack without external infrastructure:
- greenlet stub (sqlalchemy asyncio import shim; no DB I/O is performed)
- celery task stubs (trigger-scan / npmrds-sync endpoints)
- FakeAsyncSession injected via dependency_overrides (in-memory seed state)
- Auth dependencies overridden; auth logic tested directly against
  create_access_token / _enforce.

Covers every endpoint category from the README API table plus the
readings -> ensemble -> classifier -> corroboration -> dispatch pipeline
and the PM3 LOTTR/TTTR/PHED computation path.
"""

import os
import re
import sys
import types
import uuid
from collections import Counter
from datetime import datetime, timedelta

# ---------------------------------------------------------------- bootstrap
os.environ.setdefault("API_SECRET_KEY", "e2e-dummy-secret-key-32-chars-min")
os.environ.setdefault("POSTGRES_PASSWORD", "e2e-dummy")

_g = types.ModuleType("greenlet")


class _G:
    dead = True

    def switch(self, *a, **k):
        raise RuntimeError("greenlet stub")


def _getcurrent():
    return _G()


_g.getcurrent = _getcurrent
_g.greenlet = _G
sys.modules.setdefault("greenlet", _g)

_tasks_stub = types.ModuleType("app.workers.tasks")


class _Delayable:
    def __init__(self, name):
        self._name = name
        self.calls = []

    def delay(self, *a, **k):
        self.calls.append((a, k))

        class R:
            id = "e2e-task-id"

        return R()


_tasks_stub.run_anomaly_scan = _Delayable("run_anomaly_scan")
_tasks_stub.run_npmrds_sync = _Delayable("run_npmrds_sync")
sys.modules["app.workers.tasks"] = _tasks_stub

# ---------------------------------------------------------------- imports
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import security as sec  # noqa: E402
from app.db import models as M  # noqa: E402
from app.db.database import get_db_session  # noqa: E402
from app.main import app  # noqa: E402

# ---------------------------------------------------------------- fake DB


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class FakeResult:
    def __init__(self, items=None, scalar=None, rowcount=0):
        self._items = items or []
        self._scalar = scalar
        self.rowcount = rowcount

    def scalars(self):
        return _Scalars(self._items)

    def scalar(self):
        return self._scalar

    def all(self):
        return list(self._items)

    def first(self):
        if self._items:
            return self._items[0]
        return self._scalar if not isinstance(self._scalar, (int, float)) else None


_TOKEN = re.compile(r"'([^']*)'")


def _compile(stmt) -> str:
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(stmt)


def _id_tokens(s: str):
    """Quoted tokens that look like business IDs (not datetimes)."""
    out = set()
    for t in _TOKEN.findall(s):
        if ":" in t or ("-" in t and " " in t):
            continue
        if re.fullmatch(r"[A-Za-z0-9_.\-]+", t):
            out.add(t)
    return out


_SEV = {"low", "medium", "high", "critical"}
_STATUS = {"open", "resolved", "false_positive", "active", "cleared"}
_ITYPES = {"crash", "breakdown", "congestion", "wrong_way", "weather", "work_zone", "unknown"}


def _filter_anomalies(items, s):
    q = _id_tokens(s)
    out = list(items)
    st = q & _STATUS
    if st:
        out = [a for a in out if (a.status or "") in st]
    sv = q & _SEV
    if sv:
        out = [a for a in out if (a.severity or "") in sv]
    it = q & _ITYPES
    if it:
        out = [a for a in out if (a.incident_type or "") in it]
    rest = q - _STATUS - _SEV - _ITYPES
    if rest:
        out = [a for a in out if (a.sensor_id or "") in rest or (a.tmc_code or "") in rest]
    return out


def _filter_incidents(items, s):
    q = _id_tokens(s)
    out = list(items)
    st = q & _STATUS
    if st:
        out = [a for a in out if (a.status or "") in st]
    it = q & _ITYPES
    if it:
        out = [a for a in out if (a.incident_type or "") in it]
    if "IS NULL" in s and "rit_itis_id" in s:
        out = [a for a in out if not a.rit_itis_id]
    return out


def _filter_sensors(items, s):
    q = _id_tokens(s)
    out = list(items)
    rest = q - _STATUS - _SEV - _ITYPES
    stypes = {t for t in rest if t in ("loop_detector", "radar", "probe", "atr", "bluetooth", "cctv")}
    if stypes:
        out = [x for x in out if (x.sensor_type or "") in stypes]
    rest -= stypes
    if rest:
        out = [x for x in out if (x.sensor_id or "") in rest or (x.corridor_id or "") in rest]
    return out


class FakeSession:
    """In-memory stand-in for AsyncSession covering every route query."""

    def __init__(self):
        now = datetime.utcnow()
        self.anomalies = [
            M.TrafficAnomalies(
                id=uuid.uuid4(), sensor_id="loop-001", tmc_code="104N12345",
                detected_at=now - timedelta(minutes=40), anomaly_score=0.91,
                lstm_score=0.93, isoforest_score=0.87, incident_type="crash",
                severity="critical", status="open", waze_corroborated=True,
                wzdx_suppressed=False,
            ),
            M.TrafficAnomalies(
                id=uuid.uuid4(), sensor_id="radar-007", tmc_code="104N12346",
                detected_at=now - timedelta(minutes=90), anomaly_score=0.71,
                lstm_score=0.72, isoforest_score=0.68, incident_type="congestion",
                severity="medium", status="open", waze_corroborated=False,
                wzdx_suppressed=False,
            ),
            M.TrafficAnomalies(
                id=uuid.uuid4(), sensor_id="loop-001", tmc_code="104N12345",
                detected_at=now - timedelta(hours=5), anomaly_score=0.66,
                lstm_score=0.67, isoforest_score=0.60, incident_type="congestion",
                severity="low", status="resolved", waze_corroborated=False,
                wzdx_suppressed=True, resolved_at=now - timedelta(hours=4),
                clearance_time_min=60.0,
            ),
        ]
        det1 = now - timedelta(minutes=100)
        self.incidents = [
            M.IncidentEvents(
                id=uuid.uuid4(), anomaly_id=self.anomalies[0].id,
                tmc_code="104N12345", incident_type="crash", kabco_severity="A",
                detected_at=det1, cleared_at=det1 + timedelta(minutes=45),
                clearance_time_min=45.0, tim_compliant=True, lanes_blocked=2,
                status="cleared", notes="seed",
            ),
            M.IncidentEvents(
                id=uuid.uuid4(), tmc_code="104N12346", incident_type="congestion",
                detected_at=now - timedelta(minutes=30), lanes_blocked=0,
                status="active",
            ),
        ]
        self.sensors = [
            M.TrafficSensors(
                id=uuid.uuid4(), sensor_id="loop-001", sensor_type="loop_detector",
                name="I-95 Loop 1", corridor_id="I-95-N", tmc_code="104N12345",
                direction="NB", milepost=12.5,
                geometry={"type": "Point", "coordinates": [-77.0, 38.9]},
                is_active=True,
            ),
            M.TrafficSensors(
                id=uuid.uuid4(), sensor_id="radar-007", sensor_type="radar",
                name="I-95 Radar 7", corridor_id="I-95-N", tmc_code="104N12346",
                direction="NB", milepost=13.1, is_active=True,
            ),
        ]
        self.readings = [
            M.DetectorReadings(
                id=uuid.uuid4(), sensor_id="loop-001",
                timestamp=now - timedelta(minutes=5 * i),
                speed_mph=60.0 - i, volume_vph=1500.0,
                occupancy_pct=12.0 + i, headway_sec=2.0, source="detector",
            )
            for i in range(6)
        ]
        self.corridors = [
            M.Corridors(
                id=uuid.uuid4(), corridor_id="I-95-N", name="I-95 North",
                state="VA", tmc_codes=["104N12345", "104N12346"], length_miles=4.2,
            )
        ]
        base = now - timedelta(hours=2)
        self.travel_times = [
            M.TravelTimeRecords(
                id=uuid.uuid4(), tmc_code="104N12345",
                epoch_start=base + timedelta(minutes=15 * i),
                travel_time_sec=300.0 + (i % 4) * 10.0, speed_mph=55.0,
                data_density="A",
            )
            for i in range(8)
        ]
        self.pm3 = [
            M.PM3ReliabilityMetrics(
                id=uuid.uuid4(), tmc_code="104N12345", year=2024,
                lottr_am=1.2, lottr_midday=1.1, lottr_pm=1.35,
                lottr_weekend=1.05, lottr_overall=1.35, lottr_reliable=True,
                tttr=1.28, tttr_reliable=True, phed_hours=120.5,
            )
        ]
        self.wzdx = [
            M.WZDXWorkZones(
                id=uuid.uuid4(), feed_id="wz-1", state="VA",
                road_name="I-95", is_active=True,
                geometry={"type": "Point", "coordinates": [-77.05, 38.92]},
            ),
            M.WZDXWorkZones(
                id=uuid.uuid4(), feed_id="wz-2", state="MD",
                road_name="I-495", is_active=False,
            ),
        ]
        self.alerts = []
        self.audit = []
        self.by_id = {}
        self._index()

    def _index(self):
        self.by_id = {}
        for bucket in (self.anomalies, self.incidents, self.sensors):
            for o in bucket:
                self.by_id[str(o.id)] = o

    # -- session API -------------------------------------------------
    def add(self, obj):
        if isinstance(obj, M.TrafficAnomalies):
            self.anomalies.append(obj)
        elif isinstance(obj, M.IncidentEvents):
            self.incidents.append(obj)
        elif isinstance(obj, M.TrafficSensors):
            self.sensors.append(obj)
        elif isinstance(obj, M.DetectorReadings):
            self.readings.append(obj)
        elif isinstance(obj, M.TravelTimeRecords):
            self.travel_times.append(obj)
        elif isinstance(obj, M.PM3ReliabilityMetrics):
            self.pm3.append(obj)
        elif isinstance(obj, M.WZDXWorkZones):
            self.wzdx.append(obj)
        elif isinstance(obj, M.AnomalyAlerts):
            self.alerts.append(obj)
        elif isinstance(obj, M.AuditLog):
            self.audit.append(obj)
        elif isinstance(obj, M.Corridors):
            self.corridors.append(obj)
        self._index()

    async def commit(self):
        self._index()

    async def get(self, model, key):
        if model in (M.TrafficAnomalies, M.IncidentEvents, M.TrafficSensors):
            return self.by_id.get(str(key))
        return None

    async def execute(self, stmt):
        from sqlalchemy import delete as _delete  # noqa

        s = _compile(stmt)
        slow = s.lower()

        if slow.lstrip().startswith("delete"):
            return FakeResult(rowcount=5)

        if "group by" in slow:
            m = re.search(r"group by\s+\S+\.(\w+)", slow)
            col = m.group(1) if m else ""
            if "traffic_anomalies" in slow:
                items = _filter_anomalies(self.anomalies, s)
                counts = Counter(getattr(a, col, None) for a in items)
                return FakeResult(items=[(k, v) for k, v in counts.items()])
            if "incident_events" in slow:
                items = _filter_incidents(self.incidents, s)
                return FakeResult(items=[(k, v) for k, v in Counter(
                    getattr(a, "incident_type", None) for a in items).items()])
            if "wzdx_work_zones" in slow:
                return FakeResult(items=[(k, v) for k, v in Counter(
                    w.state for w in self.wzdx).items()])
            return FakeResult(items=[])

        if "count(" in slow:
            if "traffic_anomalies" in slow:
                items = _filter_anomalies(self.anomalies, s)
                if "waze_corroborated" in slow and "is true" in slow:
                    return FakeResult(scalar=sum(1 for a in items if a.waze_corroborated))
                if "wzdx_suppressed" in slow and "is true" in slow:
                    return FakeResult(scalar=sum(1 for a in items if a.wzdx_suppressed))
                return FakeResult(scalar=len(items))
            if "incident_events" in slow:
                items = _filter_incidents(self.incidents, s)
                if "clearance_time_min <=" in slow or "clearance_time_min<=" in slow.replace(" ", ""):
                    return FakeResult(scalar=sum(1 for a in items
                                                 if a.clearance_time_min is not None
                                                 and a.clearance_time_min <= 90))
                return FakeResult(scalar=len(items))
            if "wzdx_work_zones" in slow:
                items = list(self.wzdx)
                if "is true" in slow:
                    items = [w for w in items if w.is_active]
                return FakeResult(scalar=len(items))
            return FakeResult(scalar=0)

        if "avg(" in slow:
            if "clearance_time_min" in slow:
                vals = [a.clearance_time_min for a in self.incidents
                        if a.clearance_time_min is not None]
                return FakeResult(scalar=sum(vals) / len(vals) if vals else None)
            if "detector_readings" in slow:
                return FakeResult(items=[(60.0, 45000.0)])
            return FakeResult(scalar=None)

        # column / entity selects (single-column selects matched exactly so
        # they cannot shadow full-entity selects that list the same column)
        if re.search(r"SELECT\s+corridors\.tmc_codes\s+FROM", s):
            return FakeResult(items=[list(c.tmc_codes or []) for c in self.corridors])
        if "tmc_code" in slow and "travel_time_records" in slow and "distinct" in slow:
            return FakeResult(items=sorted({r.tmc_code for r in self.travel_times}))
        if "sensor_id" in slow and "traffic_sensors" in slow and "detector_readings" not in slow:
            sels = _filter_sensors(self.sensors, s)
            if re.search(r"select\s+\S+\.sensor_id\s+from", slow):
                return FakeResult(items=[x.sensor_id for x in sels])
            return FakeResult(items=sels)
        if "traffic_anomalies" in slow:
            return FakeResult(items=_filter_anomalies(self.anomalies, s))
        if "incident_events" in slow:
            return FakeResult(items=_filter_incidents(self.incidents, s))
        if "traffic_sensors" in slow and "detector_readings" not in slow:
            return FakeResult(items=_filter_sensors(self.sensors, s))
        if "detector_readings" in slow:
            q = _id_tokens(s)
            items = [r for r in self.readings if not q or r.sensor_id in q]
            return FakeResult(items=items)
        if "travel_time_records" in slow:
            q = _id_tokens(s)
            items = [r for r in self.travel_times if not q or r.tmc_code in q]
            return FakeResult(items=items)
        if "pm3_reliability_metrics" in slow:
            q = _id_tokens(s)
            items = [r for r in self.pm3 if not q or r.tmc_code in q]
            return FakeResult(items=items)
        if "corridors" in slow:
            q = _id_tokens(s)
            items = [c for c in self.corridors if not q or c.corridor_id in q or (c.state or "") in q]
            return FakeResult(items=items)
        if "corridor_performance" in slow:
            return FakeResult(items=[])
        if "wzdx_work_zones" in slow:
            items = list(self.wzdx)
            if "is true" in slow:
                items = [w for w in items if w.is_active]
            return FakeResult(items=items)
        if "anomaly_alerts" in slow:
            return FakeResult(items=list(self.alerts))
        return FakeResult(items=[])


FAKE = FakeSession()


async def _override_session():
    yield FAKE


def _allow_all():
    return "analyst"


app.dependency_overrides[get_db_session] = _override_session
for _dep in (sec.require_viewer, sec.require_analyst, sec.require_engineer, sec.require_admin):
    app.dependency_overrides[_dep] = _allow_all

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- tests

class TestHealthE2E:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "healthy"
        assert body["agent_id"] == "FHWA-AGENT-006"
        for k in ("TSMO", "TIM", "PM3", "NPMRDS", "RITIS", "Waze", "WZDx", "511"):
            assert k in body["fwa_alignment"], k

    def test_ready_and_root(self):
        assert client.get("/ready").json() == {"status": "ready"}
        root = client.get("/").json()
        assert root["port"] == 8005

    def test_openapi_builds(self):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        for p in ("/api/v1/anomalies", "/api/v1/incidents", "/api/v1/sensors",
                  "/api/v1/performance/pm3/metrics",
                  "/api/v1/performance/npmrds/travel-times",
                  "/api/v1/performance/wzdx/active"):
            assert p in paths, p


class TestSensorsE2E:
    def test_list_and_filter(self):
        r = client.get("/api/v1/sensors")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 2
        r = client.get("/api/v1/sensors", params={"sensor_type": "radar"})
        assert [s["sensor_id"] for s in r.json()] == ["radar-007"]

    def test_register_sensor(self):
        r = client.post("/api/v1/sensors", json={
            "sensor_id": "atr-100", "sensor_type": "atr",
            "name": "ATR 100", "tmc_code": "104N12347",
        })
        assert r.status_code == 200, r.text
        assert r.json()["sensor_id"] == "atr-100"
        assert len(client.get("/api/v1/sensors").json()) == 3

    def test_ingest_single_and_batch(self):
        ts = datetime.utcnow().isoformat()
        r = client.post("/api/v1/sensors/loop-001/readings", json={
            "sensor_id": "loop-001", "timestamp": ts,
            "speed_mph": 55.0, "volume_vph": 1600.0, "occupancy_pct": 14.0,
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ingested"
        batch = [{"sensor_id": "loop-001", "timestamp": ts,
                  "speed_mph": 50.0 + i, "volume_vph": 1500.0} for i in range(5)]
        r = client.post("/api/v1/sensors/readings/batch", json=batch)
        assert r.json() == {"status": "ingested", "count": 5}

    def test_batch_limit_enforced(self):
        ts = datetime.utcnow().isoformat()
        big = [{"sensor_id": "loop-001", "timestamp": ts, "speed_mph": 50.0}
               for _ in range(5001)]
        r = client.post("/api/v1/sensors/readings/batch", json=big)
        assert r.json()["status"] == "rejected"

    def test_sensor_anomaly_history(self):
        r = client.get("/api/v1/sensors/loop-001/anomalies")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 2
        assert all("severity" in a and "anomaly_score" in a for a in r.json())


class TestAnomaliesE2E:
    def test_list_and_filters(self):
        assert len(client.get("/api/v1/anomalies").json()) == 3
        r = client.get("/api/v1/anomalies", params={"severity": "critical"})
        assert len(r.json()) == 1
        r = client.get("/api/v1/anomalies", params={"status": "resolved"})
        assert len(r.json()) == 1
        r = client.get("/api/v1/anomalies", params={"incident_type": "congestion"})
        assert len(r.json()) == 2

    def test_active_threshold(self):
        r = client.get("/api/v1/anomalies/active", params={"min_severity": "high"})
        sev = {a["severity"] for a in r.json()}
        assert sev <= {"high", "critical"} and len(r.json()) == 1

    def test_summary_kpis(self):
        s = client.get("/api/v1/anomalies/summary").json()
        assert (s["total"], s["open"], s["resolved"], s["false_positives"]) == (3, 2, 1, 0)
        assert s["by_severity"]["critical"] == 1
        assert s["waze_corroborated"] == 1 and s["wzdx_suppressed"] == 1

    def test_trigger_scan_queues_task(self):
        r = client.post("/api/v1/anomalies/trigger-scan")
        assert r.json()["status"] == "scan_queued"
        assert len(_tasks_stub.run_anomaly_scan.calls) == 1

    def test_false_positive_then_resolve(self):
        target = str(FAKE.anomalies[1].id)
        r = client.post(f"/api/v1/anomalies/{target}/false-positive")
        assert r.json()["status"] == "marked_false_positive"
        assert client.get("/api/v1/anomalies/summary").json()["false_positives"] == 1
        other = str(FAKE.anomalies[0].id)
        r = client.post(f"/api/v1/anomalies/{other}/resolve")
        assert r.json()["status"] == "resolved"
        assert r.json()["clearance_time_min"] is not None
        assert client.get("/api/v1/anomalies/summary").json()["open"] == 0


class TestIncidentsE2E:
    def test_list_active_create(self):
        assert len(client.get("/api/v1/incidents").json()) == 2
        assert len(client.get("/api/v1/incidents/active").json()) == 1
        r = client.post("/api/v1/incidents", json={
            "tmc_code": "104N12345", "incident_type": "breakdown",
            "kabco_severity": "C", "lanes_blocked": 1,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "active" and body["incident_type"] == "breakdown"
        assert len(client.get("/api/v1/incidents/active").json()) == 2

    def test_tim_metrics(self):
        m = client.get("/api/v1/incidents/tim-metrics").json()
        assert m["total_incidents"] == 3
        assert m["cleared_within_90min"] == 1
        assert m["compliance_pct"] == round(100.0 / 3, 2)
        assert m["by_type"]["crash"] == 1

    def test_clearance_and_tim_flag(self):
        target = str(FAKE.incidents[1].id)
        r = client.patch(f"/api/v1/incidents/{target}/clearance", json={
            "cleared_at": datetime.utcnow().isoformat(), "lanes_blocked": 0,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "cleared"
        assert body["tim_compliant"] is True
        assert body["clearance_time_min"] is not None


class TestPerformanceE2E:
    def test_corridors_and_history(self):
        r = client.get("/api/v1/performance/corridors")
        assert r.status_code == 200 and len(r.json()) == 1
        assert r.json()[0]["tmc_codes"] == ["104N12345", "104N12346"]
        r = client.get("/api/v1/performance/corridors/I-95-N/history")
        assert r.status_code == 200 and isinstance(r.json(), list)

    def test_pm3_compute_and_list(self):
        r = client.post("/api/v1/performance/pm3/compute",
                        json={"tmc_code": "104N12345", "year": 2024})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tmc_code"] == "104N12345"
        assert body["lottr_overall"] is not None and body["tttr"] is not None
        r = client.get("/api/v1/performance/pm3/metrics", params={"year": 2024})
        assert len(r.json()) >= 1

    def test_state_summary(self):
        r = client.get("/api/v1/performance/pm3/state-summary",
                       params={"state": "VA", "year": 2024})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "VA" and body["total_tmcs"] >= 1

    def test_npmrds(self):
        r = client.get("/api/v1/performance/npmrds/travel-times",
                       params={"tmc": "104N12345"})
        assert r.status_code == 200 and len(r.json()) == 8
        r = client.post("/api/v1/performance/npmrds/sync")
        assert r.json()["status"] == "sync_queued"
        assert len(_tasks_stub.run_npmrds_sync.calls) == 1

    def test_wzdx(self):
        s = client.get("/api/v1/performance/wzdx/summary").json()
        assert (s["total"], s["active"]) == (2, 1)
        assert s["by_state"] == {"VA": 1, "MD": 1}
        assert len(client.get("/api/v1/performance/wzdx/active").json()) == 1


class TestDetectionPipelineE2E:
    """Readings -> ensemble -> classifier -> corroboration, no HTTP."""

    def _crash_window(self):
        # Free-flow baseline degrading gently, then a sudden collapse in the
        # FINAL epoch only (keeps the trailing baseline normal so the
        # speed-drop / occupancy-spike ratios exceed crash thresholds).
        window = []
        for i in range(24):
            if i >= 23:
                window.append({
                    "speed_mph": 8.0, "volume_vph": 500.0,
                    "occupancy_pct": 60.0, "headway_sec": 4.5,
                })
            else:
                window.append({
                    "speed_mph": 63.0 - i * 0.3, "volume_vph": 1600.0,
                    "occupancy_pct": 12.0 + i * 0.4, "headway_sec": 2.0,
                })
        current = {"speed_mph": 8.0, "volume_vph": 500.0,
                   "occupancy_pct": 62.0, "headway_sec": 4.5, "hour": 8}
        return window, current

    def test_crash_detected_and_classified(self):
        from app.services.corroboration import corroborate_anomaly
        from app.services.detection_pipeline import run_detection

        window, current = self._crash_window()
        waze = corroborate_anomaly(38.9, -77.0, [{
            "latitude": 38.901, "longitude": -77.001,
            "published_at": datetime.utcnow()}])
        assert waze["waze_corroborated"] is True
        res = run_detection("loop-001", window, current,
                            waze_corroborated=True, near_work_zone=False,
                            tmc_code="104N12345")
        assert res["is_anomalous"] is True
        assert res["incident_type"] == "crash"
        assert res["severity"] in ("medium", "high", "critical")
        assert res["waze_corroborated"] is True
        assert res["wzdx_suppressed"] is False

    def test_wzdx_suppresses_marginal_anomaly(self):
        from app.services import detection_pipeline as dp

        real_cls = dp.IncidentClassifier
        real_ens = dp.TrafficAnomalyEnsemble

        class MedEns:
            def detect(self, *_a, **_k):
                return {"is_anomalous": True, "anomaly_score": 0.66,
                        "lstm_score": 0.66, "isoforest_score": 0.66,
                        "severity": "medium"}

        window = [{"speed_mph": 60.0, "volume_vph": 1500.0,
                   "occupancy_pct": 12.0, "headway_sec": 2.0}] * 24
        current = {"speed_mph": 40.0, "volume_vph": 1400.0,
                   "occupancy_pct": 25.0, "headway_sec": 2.2, "hour": 10}
        try:
            dp.TrafficAnomalyEnsemble = MedEns
            dp.IncidentClassifier = real_cls
            res = dp.run_detection("loop-001", window, current,
                                   near_work_zone=True)
            assert res["wzdx_suppressed"] is True
            assert res["is_anomalous"] is False
        finally:
            dp.TrafficAnomalyEnsemble = real_ens

    def test_dispatch_511_rule(self):
        import asyncio

        from app.services import alert_dispatcher as ad

        calls = {}

        async def fake_teams(url, text, title="x"):
            calls["teams"] = text
            return True

        async def fake_pd(key, summary, severity="critical"):
            calls["pd"] = (summary, severity)
            return True

        def fake_email(to, subj, body):
            calls["email"] = subj
            return True

        async def fake_511(msg):
            calls["511"] = msg
            return True

        ad.send_teams, ad.send_pagerduty = fake_teams, fake_pd
        ad.send_email, ad.send_fiveoneone = fake_email, fake_511
        try:
            crit = {"sensor_id": "loop-001", "tmc_code": "104N12345",
                    "severity": "critical", "incident_type": "crash",
                    "anomaly_score": 0.91, "waze_corroborated": True}
            assert ad.should_push_511(crit) is True
            res = asyncio.run(ad.dispatch_anomaly(crit))
            assert res == {"teams": True, "pagerduty": True,
                           "email": True, "fiveoneone": True}
            assert "CRITICAL" in calls["teams"] and "crash" in calls["teams"]

            low = dict(crit, severity="low", waze_corroborated=False)
            assert ad.should_push_511(low) is False
            res = asyncio.run(ad.dispatch_anomaly(low))
            assert res["fiveoneone"] is False
        finally:
            import importlib
            importlib.reload(ad)


class TestAuthE2E:
    def test_jwt_roundtrip_and_roles(self):
        token = sec.create_access_token("e2e-user", role="engineer")
        payload = sec.decode_jwt_token(token)
        assert payload["sub"] == "e2e-user" and payload["role"] == "engineer"
        assert sec.check_role_minimum("analyst", "viewer") is True
        assert sec.check_role_minimum("viewer", "engineer") is False
        assert sec._enforce("viewer", "analyst") == "analyst"
        with pytest.raises(Exception) as exc:
            sec._enforce("admin", "viewer")
        assert exc.value.status_code == 403
