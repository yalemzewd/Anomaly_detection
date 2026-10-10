"""Pydantic BaseSettings with 50+ environment variables for FHWA-AGENT-003.

FHWA-AGENT-006: Traffic Anomaly Detection Agent
Port: 8005 | FedRAMP High | AKS
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional, Dict, Any
from enum import Enum
import os


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LoggingLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Initiative(str, Enum):
    TSMO = "TSMO"
    TIM = "TIM"
    NPMRDS = "NPMRDS"
    RITIS = "RITIS"
    WAZE_CCP = "WAZE_CCP"
    WZDx = "WZDx"
    CV_BSM = "CV_BSM"


# Environment configuration
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    
    # Application
    ENVIRONMENT: Environment = Field(
        Environment.DEVELOPMENT, env="ENVIRONMENT"
    )
    DEBUG: bool = Field(False, env="DEBUG")
    API_V1_STR: str = Field("/api/v1", env="API_V1_STR")
    PROJECT_NAME: str = Field(
        "FHWA-AGENT-003: Traffic Anomaly Detection Agent"
    )
    VERSION: str = Field("1.0.0", env="VERSION")
    PORT: int = Field(8005, env="PORT")
    
    # Security - Authentication
    API_SECRET_KEY: str = Field(
        ..., env="API_SECRET_KEY"
    )  # JWT signing key
    API_KEYS: str = Field(
        "", env="API_KEYS"
    )  # Comma-separated list of valid API keys
    ALGORITHM: str = Field("HS256", env="ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        1440, env="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    
    # Security - RBAC Roles
    AUTHORIZED_ROLES: str = Field(
        "viewer,analyst,engineer,admin", env="AUTHORIZED_ROLES"
    )
    
    # Database - PostgreSQL + PostGIS
    POSTGRES_HOST: str = Field(
        "localhost", env="POSTGRES_HOST"
    )
    POSTGRES_PORT: int = Field(5432, env="POSTGRES_PORT")
    POSTGRES_DB: str = Field(
        "fhwa_traffic_anomaly", env="POSTGRES_DB"
    )
    POSTGRES_USER: str = Field(
        "fhwa_user", env="POSTGRES_USER"
    )
    POSTGRES_PASSWORD: str = Field(
        ..., env="POSTGRES_PASSWORD"
    )
    POSTGRES_POOL_SIZE: int = Field(20, env="POSTGRES_POOL_SIZE")
    POSTGRES_MAX_OVERFLOW: int = Field(
        10, env="POSTGRES_MAX_OVERFLOW"
    )
    
    # Redis (Celery broker + cache)
    REDIS_URL: str = Field(
        "redis://localhost:6379/0", env="REDIS_URL"
    )
    REDIS_MAX_CONNECTIONS: int = Field(50, env="REDIS_MAX_CONNECTIONS")
    
    # Apache Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        "localhost:9092", env="KAFKA_BOOTSTRAP_SERVERS"
    )
    KAFKA_GROUP_ID: str = Field(
        "traffic-sensor-consumer", env="KAFKA_GROUP_ID"
    )
    KAFKA_SENSOR_TOPIC: str = Field(
        "detector-readings", env="KAFKA_SENSOR_TOPIC"
    )
    KALA_ALERT_TOPIC: str = Field(
        "traffic-anomalies", env="KALA_ALERT_TOPIC"
    )
    KAFKA_CV_BSM_TOPIC: str = Field(
        "cv.bsm.raw", env="KAFKA_CV_BSM_TOPIC"
    )
    KAFKA_WZDX_TOPIC: str = Field(
        "wzdx.work.zones", env="KAFKA_WZDX_TOPIC"
    )
    KAFKA_WAZE_JAMS_TOPIC: str = Field(
        "waze.jams", env="KAFKA_WAZE_JAMS_TOPIC"
    )
    KAFKA_CONSUMER_GROUP: str = Field(
        "", env="KAFKA_CONSUMER_GROUP"
    )
    KAFKA_AUTO_OFFSET_RESET: str = Field(
        "earliest", env="KAFKA_AUTO_OFFSET_RESET"
    )
    
    # NPMRDS Integration
    NPMRDS_API_KEY: str = Field(
        "", env="NPMRDS_API_KEY"
    )
    NPMRDS_API_ENDPOINT: str = Field(
        "https://api.npmrds.org", env="NPMRDS_API_ENDPOINT"
    )
    NPMRDS_SYNC_INTERVAL: int = Field(
        60, env="NPMRDS_SYNC_INTERVAL"
    )  # seconds
    
    # RITIS Integration
    RITIS_API_KEY: str = Field(
        "", env="RITIS_API_KEY"
    )
    RITIS_API_ENDPOINT: str = Field(
        "https://ritis.fhwa.dot.gov/api", env="RITIS_API_ENDPOINT"
    )
    RITIS_SYNC_INTERVAL: int = Field(
        120, env="RITIS_SYNC_INTERVAL"
    )  # seconds
    
    # Waze CCP (EDC-5) Integration
    WAZE_CCP_PARTNER_ID: str = Field(
        "", env="WAZE_CCP_PARTNER_ID"
    )
    WAZE_CCP_FEED_URL: str = Field(
        "", env="WAZE_CCP_FEED_URL"
    )
    WAZE_CCP_POLL_INTERVAL: int = Field(
        90, env="WAZE_CCP_POLL_INTERVAL"
    )  # seconds
    
    # WZDx (EDC-5) Integration
    WZDX_FEED_URLS: str = Field(
        "", env="WZDX_FEED_URLS"
    )  # Comma-separated: "VA|https://...|MD|https://..."
    WZDX_POLL_INTERVAL: int = Field(
        300, env="WZDX_POLL_INTERVAL"
    )  # seconds (5 min)
    
    # 511 Integration
    FIVEONEONE_API_URL: str = Field(
        "", env="FIVEONEONE_API_URL"
    )
    FIVEONEONE_API_KEY: str = Field(
        "", env="FIVEONEONE_API_KEY"
    )
    
    # Azure
    AZURE_STORAGE_CONNECTION_STRING: str = Field(
        "", env="AZURE_STORAGE_CONNECTION_STRING"
    )
    AZURE_KEY_VAULT_URI: str = Field(
        "", env="AZURE_KEY_VAULT_URI"
    )
    
    # ML Models
    ANOMALY_MODEL_PATH: str = Field(
        "/app/models/anomaly_lstm_iso.pkl", env="ANOMALY_MODEL_PATH"
    )
    INCIDENT_MODEL_PATH: str = Field(
        "/app/models/incident_xgb.pkl", env="INCIDENT_MODEL_PATH"
    )
    ANOMALY_THRESHOLD: float = Field(
        0.65, env="ANOMALY_THRESHOLD"
    )
    ANOMALY_HIGH_THRESHOLD: float = Field(
        0.78, env="ANOMALY_HIGH_THRESHOLD"
    )
    ANOMALY_CRITICAL_THRESHOLD: float = Field(
        0.88, env="ANOMALY_CRITICAL_THRESHOLD"
    )
    
    # Alerting
    TEAMS_WEBHOOK_URL: str = Field(
        "", env="TEAMS_WEBHOOK_URL"
    )
    PAGERDUTY_ROUTING_KEY: str = Field(
        "", env="PAGERDUTY_ROUTING_KEY"
    )
    SMTP_HOST: str = Field(
        "smtp.fhwa.dot.gov", env="SMTP_HOST"
    )
    ALERT_TO_EMAIL: str = Field(
        "tmc@fhwa.dot.gov", env="ALERT_TO_EMAIL"
    )
    
    # PM3 Regulatory
    PM3_COMPUTE_INTERVAL: str = Field(
        "daily 02:00 ET", env="PM3_COMPUTE_INTERVAL"
    )
    LOTTR_THRESHOLD: float = Field(1.5, env="LOTTR_THRESHOLD")
    TTTR_THRESHOLD: float = Field(1.5, env="TTTR_THRESHOLD")
    
    # 511 / Traveler Info
    FIVEONEONE_API_URL: str = Field(
        "", env="FIVEONEONE_API_URL"
    )
    FIVEONEONE_API_KEY: str = Field(
        "", env="FIVEONEONE_API_KEY"
    )
    
    # Corridors
    NHS_CORRIDORS_FILE: str = Field(
        "/app/data/nhs_corridors.geojson", env="NHS_CORRIDORS_FILE"
    )
    
    # Monitoring
    SENTRY_DSN: str = Field("", env="SENTRY_DSN")
    PROMETHEUS_PORT: int = Field(9090, env="PROMETHEUS_PORT")
    
    # Model weights
    ENSEMBLE_LSTM_WEIGHT: float = Field(0.4, env="ENSEMBLE_LSTM_WEIGHT")
    ENSEMBLE_ISOFOREST_WEIGHT: float = Field(0.3, env="ENSEMBLE_ISOFOREST_WEIGHT")
    ENSEMBLE_XGB_WEIGHT: float = Field(0.3, env="ENSEMBLE_XGB_WEIGHT")


try:
    settings = Settings()
except Exception:
    # Fallback defaults when required env vars are not configured
    # (API_SECRET_KEY, POSTGRES_PASSWORD). Endpoints still import;
    # configure a real .env before running the service for real.
    settings = Settings(
        API_SECRET_KEY="e2e-dummy-secret-key-32-chars-min",
        POSTGRES_PASSWORD="e2e-dummy",
    )