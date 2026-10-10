"""Dual ML pipeline: LSTM Autoencoder + IsolationForest ensemble.

Pipeline per README:
1. LSTM Autoencoder - temporal anomaly detection across 2-hour sliding
   windows (24 x 5-min epochs)
2. IsolationForest - point anomaly detection on current readings
3. Ensemble scoring - weighted combination
Heuristic fallback when serialized models are unavailable.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np


class LSTMAutoencoderDetector:
    """Temporal anomaly detector over 24-epoch (2-hour) sliding windows."""

    def __init__(
        self,
        window_epochs: int = 24,
        hidden_units: int = 64,
        threshold: float = 0.65,
    ) -> None:
        self.window_epochs = window_epochs
        self.hidden_units = hidden_units
        self.threshold = threshold
        self.is_trained = False
        self.model_version = "lstm-autoencoder-traffic-v1"
        self._model = None

    def load(self, path: str) -> bool:
        try:
            import joblib
            self._model = joblib.load(path)
            self.is_trained = True
            return True
        except Exception:
            return False

    def score_window(self, window: np.ndarray) -> Dict[str, Any]:
        """Score a (24, n_features) window. Returns score + flag."""
        if self.is_trained and self._model is not None:
            try:
                err = float(np.mean(np.abs(self._model.predict(window.reshape(1, -1)))))
                score = min(err, 1.0)
            except Exception:
                score = self._heuristic(window)
        else:
            score = self._heuristic(window)
        return {
            "lstm_score": round(score, 4),
            "is_anomalous": bool(score >= self.threshold),
            "model_version": self.model_version,
        }

    def _heuristic(self, window: np.ndarray) -> float:
        if window.size == 0:
            return 0.0
        speeds = window[:, 0] if window.ndim == 2 and window.shape[1] >= 1 else window.flatten()
        speeds = speeds[np.isfinite(speeds)]
        if len(speeds) < 3:
            return 0.0
        mean, std = float(np.mean(speeds)), float(np.std(speeds)) or 1e-6
        latest = float(speeds[-1])
        drop = max(0.0, (mean - latest) / max(mean, 1e-6))
        z = abs(latest - mean) / std
        return round(min(0.6 * drop + 0.4 * min(z / 4.0, 1.0), 1.0), 4)


class IsolationForestDetector:
    """Point anomaly detector on the current reading vector."""

    def __init__(self, contamination: float = 0.05) -> None:
        self.contamination = contamination
        self.is_trained = False
        self.model_version = "isolation-forest-traffic-v1"
        self._model = None

    def load(self, path: str) -> bool:
        try:
            import joblib
            self._model = joblib.load(path)
            self.is_trained = True
            return True
        except Exception:
            return False

    def score_point(self, features: np.ndarray) -> Dict[str, Any]:
        if self.is_trained and self._model is not None:
            try:
                raw = self._model.decision_function(features.reshape(1, -1))[0]
                score = float(1.0 / (1.0 + np.exp(raw)))
            except Exception:
                score = self._heuristic(features)
        else:
            score = self._heuristic(features)
        return {
            "isoforest_score": round(score, 4),
            "is_anomalous": bool(score >= 0.6),
            "model_version": self.model_version,
        }

    def _heuristic(self, features: np.ndarray) -> float:
        v = np.asarray(features, dtype=float).flatten()
        v = v[np.isfinite(v)]
        if len(v) == 0:
            return 0.0
        # speed drop (feature 0) + occupancy spike (feature 2)
        speed = float(v[0]) if len(v) > 0 else 60.0
        occ = float(v[2]) if len(v) > 2 else 10.0
        speed_term = max(0.0, min((60.0 - speed) / 60.0, 1.0))
        occ_term = max(0.0, min((occ - 15.0) / 60.0, 1.0))
        return round(0.6 * speed_term + 0.4 * occ_term, 4)


class TrafficAnomalyEnsemble:
    """Weighted ensemble of LSTM + IsolationForest."""

    def __init__(
        self,
        lstm_weight: float = 0.55,
        iso_weight: float = 0.45,
        threshold: float = 0.65,
        high_threshold: float = 0.78,
        critical_threshold: float = 0.88,
    ) -> None:
        total = lstm_weight + iso_weight
        self.lstm_weight = lstm_weight / total
        self.iso_weight = iso_weight / total
        self.threshold = threshold
        self.high_threshold = high_threshold
        self.critical_threshold = critical_threshold
        self.lstm = LSTMAutoencoderDetector(threshold=threshold)
        self.isoforest = IsolationForestDetector()
        self.model_version = "ensemble-lstm-iso-traffic-v1"

    def detect(
        self,
        window: np.ndarray,
        current: np.ndarray,
    ) -> Dict[str, Any]:
        lstm_res = self.lstm.score_window(window)
        iso_res = self.isoforest.score_point(current)
        lstm_score = lstm_res["lstm_score"]
        iso_score = iso_res["isoforest_score"]
        ensemble = round(
            lstm_score * self.lstm_weight + iso_score * self.iso_weight, 4
        )
        if ensemble >= self.critical_threshold:
            severity = "critical"
        elif ensemble >= self.high_threshold:
            severity = "high"
        elif ensemble >= self.threshold:
            severity = "medium"
        else:
            severity = "low"
        return {
            "is_anomalous": bool(ensemble >= self.threshold),
            "anomaly_score": ensemble,
            "lstm_score": lstm_score,
            "isoforest_score": iso_score,
            "severity": severity,
            "model_version": self.model_version,
            "timestamp": datetime.utcnow().isoformat(),
        }


def detect_traffic_anomaly(
    window: np.ndarray,
    current: np.ndarray,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience function for one-shot ensemble detection."""
    return TrafficAnomalyEnsemble(**kwargs).detect(window, current)
