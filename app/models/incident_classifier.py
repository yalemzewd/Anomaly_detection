"""XGBoost incident classifier + heuristic fallback.

Classes: crash, breakdown, congestion, wrong_way, weather, work_zone.
Features: speed drop ratio, occupancy spike, volume change, headway
variance, time-of-day encoding, anomaly score, waze corroboration flag.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

INCIDENT_CLASSES = [
    "crash",
    "breakdown",
    "congestion",
    "wrong_way",
    "weather",
    "work_zone",
    "unknown",
]


class IncidentClassifier:
    """XGBoost incident classifier with heuristic fallback."""

    def __init__(self) -> None:
        self.is_trained = False
        self.model_version = "xgb-incident-traffic-v1"
        self._model = None

    def load(self, path: str) -> bool:
        try:
            import joblib
            self._model = joblib.load(path)
            self.is_trained = True
            return True
        except Exception:
            return False

    def classify(self, features: Dict[str, float]) -> Dict[str, Any]:
        if self.is_trained and self._model is not None:
            try:
                import pandas as pd
                row = pd.DataFrame([features])
                probs = self._model.predict_proba(row)[0]
                idx = int(np.argmax(probs))
                label = INCIDENT_CLASSES[min(idx, len(INCIDENT_CLASSES) - 1)]
                return {
                    "incident_type": label,
                    "incident_confidence": round(float(probs[idx]), 4),
                    "probabilities": {
                        c: round(float(p), 4)
                        for c, p in zip(INCIDENT_CLASSES, probs)
                    },
                    "model_version": self.model_version,
                }
            except Exception:
                pass
        return self._heuristic(features)

    def _heuristic(self, f: Dict[str, float]) -> Dict[str, Any]:
        speed_drop = f.get("speed_drop_ratio", 0.0)
        occ_spike = f.get("occupancy_spike", 0.0)
        vol_change = abs(f.get("volume_change_ratio", 0.0))
        waze = f.get("waze_corroborated", 0.0)
        workzone = f.get("near_work_zone", 0.0)
        if speed_drop > 0.7 and occ_spike > 0.5 and waze > 0.5:
            label, conf = "crash", 0.82
        elif speed_drop > 0.4 and occ_spike > 0.3:
            label, conf = "congestion", 0.74
        elif workzone > 0.5 and speed_drop > 0.25:
            label, conf = "work_zone", 0.68
        elif vol_change > 0.6 and speed_drop < 0.2:
            label, conf = "breakdown", 0.55
        elif speed_drop > 0.5 and occ_spike < 0.15:
            label, conf = "weather", 0.52
        else:
            label, conf = "unknown", 0.40
        return {
            "incident_type": label,
            "incident_confidence": round(conf, 4),
            "probabilities": {label: round(conf, 4)},
            "model_version": "xgb-heuristic-fallback-v1",
        }
