"""SQLAlchemy ORM models for FHWA-AGENT-003 Traffic Anomaly Detection Agent.

13 tables as per README database schema:
1. traffic_sensors - Sensor registry (loop, radar, probe, ATR) with PostGIS geometry
2. detector_readings - Raw readings (speed, volume, occupancy, headway)
3. traffic_anomalies - Detected anomalies with ML scores, incident type, severity
4. incident_events - Incident lifecycle with KABCO severity, TIM clearance tracking
5. travel_time_records - NPMRDS 15-min epoch travel times per TMC code
6. corridors - NHS corridor definitions
7. corridor_performance - Hourly corridor performance snapshots
8. pm3_reliability_metrics - 23 CFR 490 LOTTR/TTTR/PHED results per TMC per year
9. anomaly_alerts - Multi-channel alert delivery log
10. waze_jam_records - Waze CCP jam/alert records for corroboration
11. wzdx_work_zones - WZDx active work zones for anomaly suppression
12. audit_log - Full audit trail (FedRAMP requirement)
13. ritis_events - RITIS incident pull/push sync log
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class TrafficSensors(Base):
    """Sensor registry (loop, radar, probe, ATR) with PostGIS geometry."""
    __tablename__ = "traffic_sensors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(String(64), unique=True, nullable=False, index=True)
    sensor_type = Column(String(32), nullable=False)  # loop_detector, radar, probe, atr, bluetooth, cctv
    name = Column(String(128), nullable=False)
    corridor_id = Column(String(64), nullable=True, index=True)
    tmc_code = Column(String(16), nullable=True, index=True)
    direction = Column(String(16), nullable=True)  # NB, SB, EB, WB
    milepost = Column(Float, nullable=True)
    geometry = Column(JSON, nullable=True)  # PostGIS Point {type, coordinates}
    is_active = Column(Boolean, default=True, nullable=False)
    installation_date = Column(DateTime, nullable=True)
    last_reading_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DetectorReadings(Base):
    """Raw detector readings (speed, volume, occupancy, headway)."""
    __tablename__ = "detector_readings"
    __table_args__ = (
        Index("ix_readings_sensor_time", "sensor_id", "timestamp"),
        Index("ix_readings_timestamp", "timestamp"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    speed_mph = Column(Float, nullable=True)
    volume_vph = Column(Float, nullable=True)
    occupancy_pct = Column(Float, nullable=True)
    headway_sec = Column(Float, nullable=True)
    source = Column(String(32), default="detector")  # detector, kafka, manual, bsm
    quality_score = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TrafficAnomalies(Base):
    """Detected anomalies with ML scores, incident type, severity."""
    __tablename__ = "traffic_anomalies"
    __table_args__ = (
        Index("ix_anomalies_severity_status", "severity", "status"),
        Index("ix_anomalies_tmc", "tmc_code"),
        Index("ix_anomalies_detected", "detected_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(String(64), nullable=False, index=True)
    tmc_code = Column(String(16), nullable=True, index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, index=True)
    anomaly_score = Column(Float, nullable=False)  # ensemble 0-1
    lstm_score = Column(Float, nullable=True)
    isoforest_score = Column(Float, nullable=True)
    incident_type = Column(String(32), nullable=True)  # crash, breakdown, congestion, wrong_way, weather, work_zone
    severity = Column(String(16), nullable=False, default="low")  # low, medium, high, critical
    status = Column(String(16), nullable=False, default="open")  # open, resolved, false_positive
    waze_corroborated = Column(Boolean, default=False)
    wzdx_suppressed = Column(Boolean, default=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    clearance_time_min = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class IncidentEvents(Base):
    """Incident lifecycle with KABCO severity, TIM clearance tracking."""
    __tablename__ = "incident_events"
    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_detected", "detected_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id = Column(UUID(as_uuid=True), nullable=True)
    tmc_code = Column(String(16), nullable=True, index=True)
    incident_type = Column(String(32), nullable=False)
    kabco_severity = Column(String(4), nullable=True)  # K, A, B, C, O
    detected_at = Column(DateTime(timezone=True), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)
    clearance_time_min = Column(Float, nullable=True)
    tim_compliant = Column(Boolean, nullable=True)  # 90-min clearance NUG
    lanes_blocked = Column(Integer, default=0)
    status = Column(String(16), nullable=False, default="active")
    rit_itis_id = Column(String(64), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TravelTimeRecords(Base):
    """NPMRDS 15-min epoch travel times per TMC code."""
    __tablename__ = "travel_time_records"
    __table_args__ = (
        Index("ix_tt_tmc_epoch", "tmc_code", "epoch_start"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tmc_code = Column(String(16), nullable=False, index=True)
    epoch_start = Column(DateTime(timezone=True), nullable=False, index=True)
    travel_time_sec = Column(Float, nullable=False)
    speed_mph = Column(Float, nullable=True)
    data_density = Column(String(4), nullable=True)  # A, B, C
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Corridors(Base):
    """NHS corridor definitions."""
    __tablename__ = "corridors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    corridor_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    state = Column(String(4), nullable=False)
    tmc_codes = Column(JSON, default=list)
    geometry = Column(JSON, nullable=True)
    length_miles = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CorridorPerformance(Base):
    """Hourly corridor performance snapshots."""
    __tablename__ = "corridor_performance"
    __table_args__ = (
        Index("ix_corr_perf_corridor_time", "corridor_id", "window_start"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    corridor_id = Column(String(64), nullable=False, index=True)
    window_start = Column(DateTime(timezone=True), nullable=False, index=True)
    avg_speed_mph = Column(Float, nullable=True)
    total_volume = Column(Float, nullable=True)
    anomaly_count = Column(Integer, default=0)
    incident_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PM3ReliabilityMetrics(Base):
    """23 CFR 490 LOTTR/TTTR/PHED results per TMC per year."""
    __tablename__ = "pm3_reliability_metrics"
    __table_args__ = (
        Index("ix_pm3_tmc_year", "tmc_code", "year"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tmc_code = Column(String(16), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    lottr_am = Column(Float, nullable=True)
    lottr_midday = Column(Float, nullable=True)
    lottr_pm = Column(Float, nullable=True)
    lottr_weekend = Column(Float, nullable=True)
    lottr_overall = Column(Float, nullable=True)
    lottr_reliable = Column(Boolean, nullable=True)
    tttr = Column(Float, nullable=True)
    tttr_reliable = Column(Boolean, nullable=True)
    phed_hours = Column(Float, nullable=True)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())


class AnomalyAlerts(Base):
    """Multi-channel alert delivery log."""
    __tablename__ = "anomaly_alerts"
    __table_args__ = (
        Index("ix_alerts_anomaly", "anomaly_id"),
        Index("ix_alerts_sent", "sent_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    channel = Column(String(32), nullable=False)  # teams, pagerduty, email, fiveoneone
    recipient = Column(String(256), nullable=True)
    payload = Column(JSON, default=dict)
    status = Column(String(16), default="sent")
    sent_at = Column(DateTime(timezone=True), server_default=func.now())


class WazeJamRecords(Base):
    """Waze CCP jam/alert records for corroboration."""
    __tablename__ = "waze_jam_records"
    __table_args__ = (
        Index("ix_waze_time", "published_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jam_id = Column(String(64), nullable=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    speed_mph = Column(Float, nullable=True)
    delay_sec = Column(Float, nullable=True)
    jam_level = Column(Integer, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WZDXWorkZones(Base):
    """WZDx active work zones for anomaly suppression."""
    __tablename__ = "wzdx_work_zones"
    __table_args__ = (
        Index("ix_wzdx_active", "is_active"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_id = Column(String(64), nullable=False)
    state = Column(String(4), nullable=False)
    road_name = Column(String(128), nullable=True)
    geometry = Column(JSON, nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Full audit trail (FedRAMP requirement)."""
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_time", "created_at"),
        Index("ix_audit_actor", "actor"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor = Column(String(128), nullable=False)
    action = Column(String(64), nullable=False)
    resource_type = Column(String(64), nullable=True)
    resource_id = Column(String(128), nullable=True)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RITISEvents(Base):
    """RITIS incident pull/push sync log."""
    __tablename__ = "ritis_events"
    __table_args__ = (
        Index("ix_ritis_synced", "synced_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ritis_event_id = Column(String(64), nullable=True, index=True)
    direction = Column(String(8), nullable=False)  # pull, push
    event_type = Column(String(32), nullable=True)
    payload = Column(JSON, default=dict)
    status = Column(String(16), default="synced")
    synced_at = Column(DateTime(timezone=True), server_default=func.now())
