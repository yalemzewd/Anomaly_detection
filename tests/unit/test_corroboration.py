"""Unit tests for Waze corroboration + WZDx suppression helpers."""

from datetime import datetime, timedelta

from app.services.corroboration import (
    corroborate_anomaly,
    haversine_miles,
    near_active_work_zone,
)


class TestHaversine:
    def test_same_point_zero(self):
        assert haversine_miles(38.9, -77.0, 38.9, -77.0) == 0.0

    def test_dc_to_arlington(self):
        d = haversine_miles(38.9072, -77.0369, 38.8816, -77.0910)
        assert 2.0 < d < 6.0


class TestWazeCorroboration:
    def test_match_within_radius(self):
        jams = [{"latitude": 38.901, "longitude": -77.001, "speed_mph": 10.0,
                 "published_at": datetime.utcnow()}]
        res = corroborate_anomaly(38.9, -77.0, jams)
        assert res["waze_corroborated"] is True
        assert len(res["matches"]) == 1

    def test_no_match_outside_radius(self):
        jams = [{"latitude": 39.5, "longitude": -77.5, "speed_mph": 10.0,
                 "published_at": datetime.utcnow()}]
        res = corroborate_anomaly(38.9, -77.0, jams)
        assert res["waze_corroborated"] is False

    def test_stale_jam_ignored(self):
        jams = [{"latitude": 38.901, "longitude": -77.001,
                 "published_at": datetime.utcnow() - timedelta(hours=2)}]
        res = corroborate_anomaly(38.9, -77.0, jams, max_age_min=20)
        assert res["waze_corroborated"] is False

    def test_missing_coords(self):
        assert corroborate_anomaly(None, None, [])["waze_corroborated"] is False


class TestWZDx:
    def test_near_active_zone(self):
        zones = [{"is_active": True,
                  "geometry": {"type": "Point", "coordinates": [-77.001, 38.901]}}]
        res = near_active_work_zone(38.9, -77.0, zones)
        assert res["near_work_zone"] is True

    def test_inactive_zone_ignored(self):
        zones = [{"is_active": False,
                  "geometry": {"type": "Point", "coordinates": [-77.001, 38.901]}}]
        assert near_active_work_zone(38.9, -77.0, zones)["near_work_zone"] is False
