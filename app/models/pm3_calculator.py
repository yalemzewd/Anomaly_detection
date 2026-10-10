"""PM3 reliability calculator per 23 CFR Part 490 Subparts E and F.

- LOTTR (Level of Travel Time Reliability): 80th / 50th percentile travel
  time for 4 time periods. Threshold <= 1.50 = reliable.
  - Weekday AM peak: 6:00-9:59 AM
  - Weekday Midday: 10:00 AM-3:59 PM
  - Weekday PM peak: 4:00-7:59 PM
  - Weekend: all day Saturday and Sunday
- TTTR (Truck TTR): 95th / 50th percentile. Threshold <= 1.50.
- PHED (Peak Hours of Excessive Delay): annual person-hours of excessive
  delay per capita.
Results feed HPMS biennial state performance reports (MAP-21/FAST Act).
"""

from datetime import datetime, time
from typing import Dict, List, Optional

import numpy as np

LOTTR_THRESHOLD = 1.5
TTTR_THRESHOLD = 1.5


def _period_of(dt: datetime) -> Optional[str]:
    wd = dt.weekday()  # 0=Mon
    t = dt.time()
    if wd >= 5:
        return "weekend"
    if time(6, 0) <= t <= time(9, 59):
        return "am"
    if time(10, 0) <= t <= time(15, 59):
        return "midday"
    if time(16, 0) <= t <= time(19, 59):
        return "pm"
    return None


def _ratio(p_high: float, p50: float) -> Optional[float]:
    if p50 is None or p50 <= 0 or p_high is None:
        return None
    return round(float(p_high / p50), 4)


def compute_lottr(records: List[Dict]) -> Dict[str, Optional[float]]:
    """Compute LOTTR for the 4 time periods from 15-min epoch records.

    records: list of {"epoch_start": datetime, "travel_time_sec": float}
    """
    buckets: Dict[str, List[float]] = {"am": [], "midday": [], "pm": [], "weekend": []}
    for r in records:
        ts = r.get("epoch_start")
        tt = r.get("travel_time_sec")
        if ts is None or tt is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue
        period = _period_of(ts)
        if period:
            buckets[period].append(float(tt))
    out: Dict[str, Optional[float]] = {}
    for period, values in buckets.items():
        if len(values) < 4:
            out[period] = None
            continue
        p50 = float(np.percentile(values, 50))
        p80 = float(np.percentile(values, 80))
        out[period] = _ratio(p80, p50)
    vals = [v for v in out.values() if v is not None]
    out["overall"] = round(float(max(vals)), 4) if vals else None
    out["reliable"] = bool(out["overall"] is not None and out["overall"] <= LOTTR_THRESHOLD)
    return out


def compute_tttr(records: List[Dict]) -> Dict[str, Optional[float]]:
    """Compute Truck TTR (95th/50th) from truck travel time records."""
    values = [
        float(r["travel_time_sec"])
        for r in records
        if r.get("travel_time_sec") is not None
    ]
    if len(values) < 4:
        return {"tttr": None, "reliable": None}
    p50 = float(np.percentile(values, 50))
    p95 = float(np.percentile(values, 95))
    ratio = _ratio(p95, p50)
    return {
        "tttr": ratio,
        "reliable": bool(ratio is not None and ratio <= TTTR_THRESHOLD),
    }


def compute_phed(
    records: List[Dict],
    threshold_speed_mph: float = 20.0,
    segment_length_miles: float = 1.0,
    avg_occupancy: float = 1.7,
) -> Optional[float]:
    """Simplified PHED: sum of excessive delay person-hours over peak periods."""
    total = 0.0
    for r in records:
        speed = r.get("speed_mph")
        vol = r.get("volume_vph", 0.0) or 0.0
        if speed is None or speed <= 0:
            continue
        if speed < threshold_speed_mph:
            # delay per vehicle-hour = (1/speed - 1/threshold) * length
            delay_hr = (1.0 / speed - 1.0 / threshold_speed_mph) * segment_length_miles
            total += max(0.0, delay_hr) * vol * avg_occupancy * 0.25  # 15-min epoch
    return round(total, 2)
