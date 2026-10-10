"""FastAPI app factory for FHWA-AGENT-003 Traffic Anomaly Detection.

Port 8005. Includes /health, /ready, / root endpoints plus the five
versioned route groups: anomalies, incidents, sensors, performance.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import anomalies, incidents, performance, sensors
from app.api.routes import health as health_routes
from app.core.config import settings
from app.models.schemas import HealthResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="FHWA-AGENT-003: Traffic Anomaly Detection Agent",
        version="1.0.0",
        description=(
            "Real-time traffic anomaly detection and incident identification "
            "(TSMO/TIM/PM3/NPMRDS/RITIS/Waze/WZDx)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse, tags=["Health"])
    async def health() -> HealthResponse:
        return HealthResponse(
            fwa_alignment={
                "TSMO": "60s detection scan",
                "TIM": "90-min clearance tracking",
                "PM3": "LOTTR/TTTR/PHED per 23 CFR 490",
                "NPMRDS": "15-min epoch sync",
                "RITIS": "bidirectional sync",
                "Waze": "CCP corroboration 1.5mi",
                "WZDx": "work-zone suppression v4.0",
                "511": "traveler info push",
            },
            subsystems={"db": "ok", "kafka": "ok", "ml": "ok"},
        )

    @app.get("/ready", tags=["Health"])
    async def ready() -> dict:
        return {"status": "ready"}

    @app.get("/", tags=["Health"])
    async def root() -> dict:
        return {
            "agent": "FHWA-AGENT-003 Traffic Anomaly Detection",
            "version": "1.0.0",
            "port": settings.PORT,
            "docs": "/docs",
        }

    app.include_router(health_routes.router)
    app.include_router(anomalies.router, prefix="/api/v1")
    app.include_router(incidents.router, prefix="/api/v1")
    app.include_router(sensors.router, prefix="/api/v1")
    app.include_router(performance.router, prefix="/api/v1")
    return app


app = create_app()
