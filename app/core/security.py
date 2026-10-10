"""JWT + API Key authentication with 4-role RBAC for FHWA-AGENT-003.

FHWA-AGENT-006: Traffic Anomaly Detection Agent
Port: 8005 | FedRAMP High | AKS
"""

import jwt
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict, Any

from app.core.config import settings

# 4-role RBAC hierarchy: viewer(0) → analyst(1) → engineer(2) → admin(3)
ROLE_HIERARCHY: Dict[str, int] = {
    "viewer": 0,
    "analyst": 1,
    "engineer": 2,
    "admin": 3,
}

# Sensor types and their properties (from README)
SENSOR_TYPES: Dict[str, Dict[str, Any]] = {
    "loop_detector": {
        "name": "Loop Detector",
        "measurements": ["speed", "volume", "occupancy", "headway"],
        "units": {"speed": "mph", "volume": "vehicles/hour", "occupancy": "%", "headway": "seconds"},
    },
    "radar": {
        "name": "Radar Sensor",
        "measurements": ["speed", "volume", "occupancy", "vehicle_length"],
        "units": {"speed": "mph", "volume": "vehicles/hour", "occupancy": "%", "vehicle_length": "feet"},
    },
    "probe": {
        "name": "Probe Sensor",
        "measurements": ["speed", "location", "confidence"],
        "units": {"speed": "mph", "location": "gps_coords", "confidence": "0-1"},
    },
    "atr": {
        "name": "Automated Traffic Recorder",
        "measurements": ["volume", "speed", "classification"],
        "units": {"volume": "vehicles/day", "speed": "mph", "classification": "vehicle_type"},
    },
    "bluetooth": {
        "name": "Bluetooth Tracker",
        "measurements": ["travel_time", "speed"],
        "units": {"travel_time": "seconds", "speed": "mph"},
    },
    "cctv": {
        "name": "CCTV Camera",
        "measurements": ["image", "vehicle_count", "queue_length"],
        "units": {"vehicle_count": "count", "queue_length": "feet"},
    },
}


def get_role_level(role: str) -> int:
    """Get the numeric level for an RBAC role."""
    return ROLE_HIERARCHY.get(role, -1)


def check_role_minimum(user_role: str, minimum_role: str) -> bool:
    """Check if user's role meets the minimum required role."""
    user_level = get_role_level(user_role)
    min_level = get_role_level(minimum_role)
    return user_level >= min_level and user_level >= 0


def verify_api_key(x_api_key: str = Header(...)) -> str:
    """Verify API key from X-API-Key header."""
    valid_keys = [k.strip() for k in settings.API_KEYS.split(",") if k.strip()]
    if x_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return x_api_key


def verify_jwt(token: str = Header(...)) -> Dict[str, Any]:
    """Verify Bearer JWT token and return payload."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    
    try:
        payload = jwt.decode(
            token,
            settings.API_SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def get_current_user_role(payload: Dict[str, Any] = Depends(verify_jwt)) -> str:
    """Extract user role from JWT payload."""
    role = payload.get("role", "viewer")
    return role


def _enforce(required_role: str, current_role: str) -> str:
    """Enforce minimum role; separated so it is directly unit-testable."""
    if not check_role_minimum(current_role, required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required: {required_role}, "
            f"Have: {current_role}",
        )
    return current_role


def check_permission(required_role: str, current_role: str = Depends(get_current_user_role)) -> str:
    """FastAPI dependency to check if current user has required role minimum."""
    return _enforce(required_role, current_role)


# Convenience dependencies for each role level
def require_viewer(current_role: str = Depends(get_current_user_role)) -> str:
    """Require viewer role or higher."""
    return _enforce("viewer", current_role)


def require_analyst(current_role: str = Depends(get_current_user_role)) -> str:
    """Require analyst role or higher."""
    return _enforce("analyst", current_role)


def require_engineer(current_role: str = Depends(get_current_user_role)) -> str:
    """Require engineer role or higher."""
    return _enforce("engineer", current_role)


def require_admin(current_role: str = Depends(get_current_user_role)) -> str:
    """Require admin role only."""
    return _enforce("admin", current_role)


def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    role: str = "viewer",
) -> str:
    """Create a JWT access token with specified role."""
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    
    to_encode = {
        "exp": expire,
        "iat": now,
        "sub": str(subject),
        "role": role,
    }
    
    encoded = jwt.encode(
        to_encode,
        settings.API_SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded


def decode_jwt_token(token: str) -> Dict[str, Any]:
    """Decode a JWT token without verification (for internal use)."""
    try:
        payload = jwt.decode(
            token,
            settings.API_SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except Exception:
        return {}


# Token authentication helper for backward compatibility
def authenticate_credentials(
    api_key: str = Depends(verify_api_key),
    payload: Dict[str, Any] = Depends(verify_jwt),
) -> tuple[str, Dict[str, Any]]:
    """Return (api_key, payload) for authenticated requests."""
    return (api_key, payload)