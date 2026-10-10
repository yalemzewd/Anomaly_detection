"""Waze CCP corroboration + WZDx work-zone suppression helpers.

- corroborate_anomaly(): checks Waze jams within a 1.5-mile radius of the
  sensor location and recent time window.
- near_active_work_zone(): checks WZDx work zones near the sensor; anomalies
  near active work zones are suppressed (expected slowdowns).
"""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


def haversine_miles(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    r = 3958.8  # earth radius miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def corroborate_anomaly(
    sensor_lat: Optional[float],
    sensor_lon: Optional[float],
    jams: List[Dict[str, Any]],
    radius_miles: float = 1.5,
    max_age_min: int = 20,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Check Waze jams within radius. Returns corroboration flag + matches."""
    now = now or datetime.utcnow()
    if sensor_lat is None or sensor_lon is None:
        return {"waze_corroborated": False, "matches": []}
    matches = []
    for jam in jams:
        lat, lon = jam.get("latitude"), jam.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            dist = haversine_miles(sensor_lat, sensor_lon, float(lat), float(lon))
        except (TypeError, ValueError):
            continue
        if dist > radius_miles:
            continue
        pub = jam.get("published_at")
        if isinstance(pub, str):
            try:
                pub = datetime.fromisoformat(pub)
            except ValueError:
                pub = None
        if pub is not None and (now - pub) > timedelta(minutes=max_age_min):
            continue
        matches.append({**jam, "distance_miles": round(dist, 3)})
    return {"waze_corroborated": bool(matches), "matches": matches}


def near_active_work_zone(
    sensor_lat: Optional[float],
    sensor_lon: Optional[float],
    work_zones: List[Dict[str, Any]],
    radius_miles: float = 1.0,
) -> Dict[str, Any]:
    """Check whether sensor is near an active WZDx work zone."""
    if sensor_lat is None or sensor_lon is None:
        return {"near_work_zone": False, "matches": []}
    matches = []
    for wz in work_zones:
        if not wz.get("is_active", True):
            continue
        geom = wz.get("geometry") or {}
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if not coords or len(coords) < 2:
            continue
        try:
            dist = haversine_miles(sensor_lat, sensor_lon, float(coords[1]), float(coords[0]))
        except (TypeError, ValueError, IndexError):
            continue
        if dist <= radius_miles:
            matches.append({**wz, "distance_miles": round(dist, 3)})
    return {"near_work_zone": bool(matches), "matches": matches}
