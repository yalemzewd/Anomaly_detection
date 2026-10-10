"""Unit tests for the dual ML anomaly ensemble."""

import numpy as np

from app.models.anomaly_detector import (
    IsolationForestDetector,
    LSTMAutoencoderDetector,
    TrafficAnomalyEnsemble,
    detect_traffic_anomaly,
)


def _window(n=24, speed=62.0, occ=12.0):
    speeds = np.linspace(speed, speed - 5, n)
    vols = np.full(n, 1500.0)
    occs = np.linspace(occ, occ + 5, n)
    heads = np.full(n, 2.0)
    return np.column_stack([speeds, vols, occs, heads])


class TestLSTM:
    def test_init_defaults(self):
        d = LSTMAutoencoderDetector()
        assert d.window_epochs == 24
        assert not d.is_trained

    def test_heuristic_normal(self):
        d = LSTMAutoencoderDetector()
        res = d.score_window(_window())
        assert 0.0 <= res["lstm_score"] <= 1.0
        assert res["is_anomalous"] is False

    def test_heuristic_crash(self):
        d = LSTMAutoencoderDetector()
        w = _window()
        w[-1, 0] = 8.0  # sudden speed collapse
        res = d.score_window(w)
        assert res["lstm_score"] > 0.3

    def test_load_missing_returns_false(self):
        assert LSTMAutoencoderDetector().load("/nonexistent/model.pkl") is False


class TestIsolationForest:
    def test_init(self):
        assert IsolationForestDetector().contamination == 0.05

    def test_heuristic_normal(self):
        res = IsolationForestDetector().score_point(np.array([62.0, 1500.0, 12.0, 2.0]))
        assert res["isoforest_score"] < 0.5

    def test_heuristic_congested(self):
        res = IsolationForestDetector().score_point(np.array([12.0, 2200.0, 55.0, 1.1]))
        assert res["isoforest_score"] > 0.4


class TestEnsemble:
    def test_weights_normalized(self):
        e = TrafficAnomalyEnsemble(lstm_weight=0.55, iso_weight=0.45)
        assert abs(e.lstm_weight + e.iso_weight - 1.0) < 1e-9

    def test_normal_reading_not_anomalous(self):
        e = TrafficAnomalyEnsemble()
        res = e.detect(_window(), np.array([60.0, 1500.0, 12.0, 2.0]))
        assert res["is_anomalous"] is False
        assert res["severity"] == "low"

    def test_crash_reading_anomalous(self):
        e = TrafficAnomalyEnsemble()
        w = _window()
        w[-3:, 0] = [20.0, 12.0, 8.0]
        w[-3:, 2] = [45.0, 55.0, 62.0]
        res = e.detect(w, np.array([8.0, 600.0, 62.0, 4.5]))
        assert res["is_anomalous"] is True
        assert res["severity"] in ("medium", "high", "critical")
        assert 0.0 <= res["anomaly_score"] <= 1.0

    def test_convenience_fn(self):
        res = detect_traffic_anomaly(_window(), np.array([60.0, 1500.0, 12.0, 2.0]))
        assert "anomaly_score" in res and "severity" in res
