"""Core detection pipeline: readings -> ensemble -> classifier -> Waze/WZDx -> persist.

Steps:
1. Build 24-epoch window + current feature vector per sensor.
2. Ensemble anomaly scoring (LSTM + IsolationForest).
3. XGBoost incident classification on flagged readings.
4. Waze CCP corroboration (jams within 1.5-mile radius).
5. WZDx suppression (near active work zones).
6. Severity assignment + persistence payload.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from app.models.anomaly_detector import TrafficAnomalyEnsemble
from app.models.incident_classifier import IncidentClassifier


def build_features(
    window: List[Dict[str, Any]],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Build window matrix, current vector, and classifier features."""
    speeds = np.array([r.get("speed_mph") or 0.0 for r in window], dtype=float)
    occs = np.array([r.get("occupancy_pct") or 0.0 for r in window], dtype=float)
    vols = np.array([r.get("volume_vph") or 0.0 for r in window], dtype=float)
    heads = np.array([r.get("headway_sec") or 0.0 for r in window], dtype=float)
    mat = np.column_stack([speeds, vols, occs, heads]) if len(speeds) else np.zeros((0, 4))

    cur_speed = float(current.get("speed_mph") or 0.0)
    cur_occ = float(current.get("occupancy_pct") or 0.0)
    cur_vol = float(current.get("volume_vph") or 0.0)
    cur_head = float(current.get("headway_sec") or 0.0)
    current_vec = np.array([cur_speed, cur_vol, cur_occ, cur_head], dtype=float)

    base_speed = float(np.mean(speeds[-6:])) if len(speeds) >= 6 else (float(np.mean(speeds)) if len(speeds) else 60.0)
    speed_drop = max(0.0, (base_speed - cur_speed) / max(base_speed, 1e-6))
    base_occ = float(np.mean(occs[-6:])) if len(occs) >= 6 else (float(np.mean(occs)) if len(occs) else 10.0)
    occ_spike = max(0.0, (cur_occ - base_occ) / 60.0)
    base_vol = float(np.mean(vols[-6:])) if len(vols) >= 6 else (float(np.mean(vols)) if len(vols) else cur_vol or 1.0)
    vol_change = (cur_vol - base_vol) / max(base_vol, 1e-6)
    hour = current.get("hour", datetime.utcnow().hour)
    tod = float(hour) / 24.0

    classifier_features = {
        "speed_drop_ratio": round(speed_drop, 4),
        "occupancy_spike": round(occ_spike, 4),
        "volume_change_ratio": round(vol_change, 4),
        "headway_sec": round(cur_head, 3),
        "time_of_day": round(tod, 4),
        "anomaly_score": 0.0,  # filled after ensemble
        "waze_corroborated": 0.0,
        "near_work_zone": 0.0,
    }
    return {"matrix": mat, "current_vec": current_vec, "classifier_features": classifier_features}


def run_detection(
    sensor_id: str,
    window: List[Dict[str, Any]],
    current: Dict[str, Any],
    ensemble: Optional[TrafficAnomalyEnsemble] = None,
    classifier: Optional[IncidentClassifier] = None,
    waze_corroborated: bool = False,
    near_work_zone: bool = False,
    tmc_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full detection pipeline for one sensor reading."""
    ensemble = ensemble or TrafficAnomalyEnsemble()
    classifier = classifier or IncidentClassifier()
    feats = build_features(window, current)
    det = ensemble.detect(feats["matrix"], feats["current_vec"])
    cf = feats["classifier_features"]
    cf["anomaly_score"] = det["anomaly_score"]
    cf["waze_corroborated"] = 1.0 if waze_corroborated else 0.0
    cf["near_work_zone"] = 1.0 if near_work_zone else 0.0
    cls = classifier.classify(cf) if det["is_anomalous"] else {
        "incident_type": "unknown",
        "incident_confidence": 0.0,
        "model_version": "skipped-below-threshold",
    }
    suppressed = bool(near_work_zone and det["severity"] in ("low", "medium"))
    return {
        "sensor_id": sensor_id,
        "tmc_code": tmc_code,
        "detected_at": datetime.utcnow().isoformat(),
        "is_anomalous": det["is_anomalous"] and not suppressed,
        "anomaly_score": det["anomaly_score"],
        "lstm_score": det["lstm_score"],
        "isoforest_score": det["isoforest_score"],
        "severity": det["severity"],
        "incident_type": cls["incident_type"],
        "incident_confidence": cls.get("incident_confidence", 0.0),
        "waze_corroborated": waze_corroborated,
        "wzdx_suppressed": suppressed,
    }
