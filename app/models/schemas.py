"""Pydantic v2 request/response schemas for FHWA-AGENT-003 Traffic Anomaly Detection."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SensorType(str, Enum):
    LOOP_DETECTOR = "loop_detector"
    RADAR = "radar"
    PROBE = "probe"
    ATR = "atr"
    BLUETOOTH = "bluetooth"
    CCTV = "cctv"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentType(str, Enum):
    CRASH = "crash"
    BREAKDOWN = "breakdown"
    CONGESTION = "congestion"
    WRONG_WAY = "wrong_way"
    WEATHER = "weather"
    WORK_ZONE = "work_zone"
    UNKNOWN = "unknown"


class IncidentStatus(str, Enum):
    ACTIVE = "active"
    VERIFIED = "verified"
    CLEARED = "cleared"
    DISMISSED = "dismissed"


class SensorCreate(BaseModel):
    sensor_id: str = Field(..., description="Unique sensor identifier")
    sensor_type: SensorType = Field(..., description="Sensor hardware type")
    name: str = Field(..., description="Human-readable sensor name")
    corridor_id: Optional[str] = Field(None, description="NHS corridor ID")
    tmc_code: Optional[str] = Field(None, description="NPMRDS TMC code")
    direction: Optional[str] = Field(None, description="NB/SB/EB/WB")
    milepost: Optional[float] = Field(None, description="Milepost location")
    geometry: Optional[Dict[str, Any]] = Field(None, description="GeoJSON Point")
    is_active: bool = Field(True, description="Whether sensor is active")


class SensorResponse(SensorCreate):
    id: str
    installation_date: Optional[datetime] = None
    last_reading_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ReadingIngest(BaseModel):
    sensor_id: str = Field(..., description="Sensor ID")
    timestamp: datetime = Field(..., description="Reading timestamp UTC")
    speed_mph: Optional[float] = Field(None, ge=0, le=150)
    volume_vph: Optional[float] = Field(None, ge=0)
    occupancy_pct: Optional[float] = Field(None, ge=0, le=100)
    headway_sec: Optional[float] = Field(None, ge=0)
    source: str = Field("detector", description="detector|kafka|manual|bsm")
    quality_score: float = Field(1.0, ge=0.0, le=1.0)


class AnomalyDetectionResult(BaseModel):
    is_anomalous: bool
    anomaly_score: float = Field(ge=0.0, le=1.0)
    lstm_score: float = Field(ge=0.0, le=1.0)
    isoforest_score: float = Field(ge=0.0, le=1.0)
    incident_type: IncidentType = IncidentType.UNKNOWN
    incident_confidence: float = Field(0.0, ge=0.0, le=1.0)
    severity: Severity = Severity.LOW
    waze_corroborated: bool = False
    wzdx_suppressed: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class IncidentCreate(BaseModel):
    anomaly_id: Optional[str] = None
    tmc_code: Optional[str] = None
    incident_type: IncidentType
    kabco_severity: Optional[str] = Field(None, description="K/A/B/C/O")
    lanes_blocked: int = Field(0, ge=0)
    notes: Optional[str] = None


class IncidentResponse(IncidentCreate):
    id: str
    detected_at: datetime
    verified_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    clearance_time_min: Optional[float] = None
    tim_compliant: Optional[bool] = None
    status: IncidentStatus = IncidentStatus.ACTIVE
    rit_itis_id: Optional[str] = None


class ClearanceUpdate(BaseModel):
    cleared_at: datetime = Field(default_factory=datetime.utcnow)
    lanes_blocked: int = Field(0, ge=0)
    notes: Optional[str] = None


class TIMMetrics(BaseModel):
    total_incidents: int
    cleared_within_90min: int
    compliance_pct: float
    avg_clearance_min: Optional[float] = None
    by_type: Dict[str, int] = Field(default_factory=dict)


class PM3ComputeRequest(BaseModel):
    tmc_code: str
    year: int = Field(..., ge=2000, le=2100)


class PM3MetricsResponse(BaseModel):
    tmc_code: str
    year: int
    lottr_am: Optional[float] = None
    lottr_midday: Optional[float] = None
    lottr_pm: Optional[float] = None
    lottr_weekend: Optional[float] = None
    lottr_overall: Optional[float] = None
    lottr_reliable: Optional[bool] = None
    tttr: Optional[float] = None
    tttr_reliable: Optional[bool] = None
    phed_hours: Optional[float] = None


class StateSummaryResponse(BaseModel):
    state: str
    year: int
    total_tmcs: int
    reliable_tmcs: int
    reliability_pct: float
    avg_lottr: Optional[float] = None
    avg_tttr: Optional[float] = None
    total_phed: Optional[float] = None


class WazeJam(BaseModel):
    jam_id: Optional[str] = None
    latitude: float
    longitude: float
    speed_mph: Optional[float] = None
    delay_sec: Optional[float] = None
    jam_level: Optional[int] = None
    published_at: datetime


class AnomalySummary(BaseModel):
    total: int
    open: int
    resolved: int
    false_positives: int
    by_severity: Dict[str, int] = Field(default_factory=dict)
    by_type: Dict[str, int] = Field(default_factory=dict)
    waze_corroborated: int = 0
    wzdx_suppressed: int = 0


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    agent_id: str = "FHWA-AGENT-006"
    port: int = 8005
    fwa_alignment: Dict[str, str] = Field(default_factory=dict)
    subsystems: Dict[str, str] = Field(default_factory=dict)
