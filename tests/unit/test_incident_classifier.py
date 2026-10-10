"""Unit tests for XGBoost incident classifier (heuristic fallback)."""

from app.models.incident_classifier import INCIDENT_CLASSES, IncidentClassifier


class TestIncidentClassifier:
    def test_classes_defined(self):
        for c in ("crash", "breakdown", "congestion", "wrong_way", "weather", "work_zone"):
            assert c in INCIDENT_CLASSES

    def test_crash_heuristic(self):
        clf = IncidentClassifier()
        res = clf.classify({
            "speed_drop_ratio": 0.8, "occupancy_spike": 0.6,
            "volume_change_ratio": -0.5, "headway_sec": 4.0,
            "time_of_day": 0.35, "anomaly_score": 0.9,
            "waze_corroborated": 1.0, "near_work_zone": 0.0,
        })
        assert res["incident_type"] == "crash"
        assert res["incident_confidence"] > 0.5

    def test_congestion_heuristic(self):
        clf = IncidentClassifier()
        res = clf.classify({
            "speed_drop_ratio": 0.5, "occupancy_spike": 0.4,
            "volume_change_ratio": 0.1, "headway_sec": 1.2,
            "time_of_day": 0.35, "anomaly_score": 0.7,
            "waze_corroborated": 0.0, "near_work_zone": 0.0,
        })
        assert res["incident_type"] == "congestion"

    def test_work_zone_heuristic(self):
        clf = IncidentClassifier()
        res = clf.classify({
            "speed_drop_ratio": 0.3, "occupancy_spike": 0.2,
            "volume_change_ratio": -0.1, "headway_sec": 2.5,
            "time_of_day": 0.5, "anomaly_score": 0.66,
            "waze_corroborated": 0.0, "near_work_zone": 1.0,
        })
        assert res["incident_type"] == "work_zone"

    def test_load_missing_returns_false(self):
        assert IncidentClassifier().load("/nonexistent/xgb.pkl") is False
