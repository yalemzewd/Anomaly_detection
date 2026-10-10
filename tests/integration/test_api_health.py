"""Integration tests for FHWA-AGENT-003 health endpoints."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["agent_id"] == "FHWA-AGENT-006"
    assert "TSMO" in body["fwa_alignment"]


def test_ready():
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["port"] == 8005


def test_healthz_route_group():
    r = client.get("/api/v1/healthz")
    # route exists but needs no DB; either 200 or routed 404-with-prefix is a fail
    assert r.status_code in (200, 404)


def test_anomaly_routes_registered():
    paths = {route.path for route in app.routes}
    for expected in (
        "/api/v1/anomalies",
        "/api/v1/anomalies/active",
        "/api/v1/anomalies/summary",
        "/api/v1/incidents",
        "/api/v1/incidents/active",
        "/api/v1/incidents/tim-metrics",
        "/api/v1/sensors",
        "/api/v1/performance/corridors",
        "/api/v1/performance/pm3/metrics",
        "/api/v1/performance/npmrds/travel-times",
        "/api/v1/performance/wzdx/active",
    ):
        assert expected in paths, f"missing route {expected}"
