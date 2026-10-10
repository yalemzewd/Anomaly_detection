"""Health routes (duplicates main-level probes for route-group parity)."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "healthy"}


@router.get("/readyz")
async def readyz() -> dict:
    return {"status": "ready"}
