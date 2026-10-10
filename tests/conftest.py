"""Shared fixtures for FHWA-AGENT-003 tests."""

import pytest


@pytest.fixture
def sample_reading():
    from datetime import datetime
    return {
        "sensor_id": "loop-001",
        "timestamp": datetime(2026, 1, 5, 8, 0, 0),
        "speed_mph": 28.0,
        "volume_vph": 1800.0,
        "occupancy_pct": 32.0,
        "headway_sec": 2.1,
        "source": "detector",
        "quality_score": 1.0,
    }


@pytest.fixture
def sample_window(sample_reading):
    window = []
    for i in range(24):
        r = dict(sample_reading)
        r["speed_mph"] = 62.0 - i * 0.4
        r["occupancy_pct"] = 12.0 + i * 0.5
        window.append(r)
    return window


@pytest.fixture
def ensemble():
    from app.models.anomaly_detector import TrafficAnomalyEnsemble
    return TrafficAnomalyEnsemble()


@pytest.fixture
def classifier():
    from app.models.incident_classifier import IncidentClassifier
    return IncidentClassifier()


@pytest.fixture
def npmrds_records():
    from datetime import datetime, timedelta
    base = datetime(2025, 3, 4, 7, 0, 0)  # Tuesday AM peak
    recs = []
    for i in range(48):
        ts = base + timedelta(minutes=15 * i)
        recs.append({"epoch_start": ts, "travel_time_sec": 300.0 + (i % 8) * 12.0})
    return recs
