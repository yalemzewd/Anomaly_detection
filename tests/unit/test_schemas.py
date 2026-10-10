"""Unit tests for Pydantic schemas."""

from datetime import datetime

from app.models.schemas import (
    IncidentCreate,
    IncidentType,
    ReadingIngest,
    SensorCreate,
    SensorType,
    Severity,
)


class TestSensorCreate:
    def test_valid(self):
        s = SensorCreate(sensor_id="loop-001", sensor_type=SensorType.LOOP_DETECTOR,
                         name="I-95 Loop 1", tmc_code="104N12345")
        assert s.sensor_id == "loop-001"
        assert s.is_active is True

    def test_geometry_optional(self):
        s = SensorCreate(sensor_id="r-1", sensor_type="radar", name="R")
        assert s.geometry is None


class TestReadingIngest:
    def test_valid(self):
        r = ReadingIngest(sensor_id="loop-001", timestamp=datetime.utcnow(),
                          speed_mph=55.0, volume_vph=1600.0, occupancy_pct=14.0)
        assert r.speed_mph == 55.0

    def test_speed_bounds(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ReadingIngest(sensor_id="x", timestamp=datetime.utcnow(), speed_mph=999.0)


class TestIncidentCreate:
    def test_kabco_optional(self):
        i = IncidentCreate(incident_type=IncidentType.CRASH)
        assert i.kabco_severity is None
        assert i.lanes_blocked == 0
