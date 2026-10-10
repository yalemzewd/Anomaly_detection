"""Unit tests for PM3 reliability calculator (23 CFR Part 490)."""

from datetime import datetime, timedelta

from app.models.pm3_calculator import compute_lottr, compute_phed, compute_tttr


def _epoch_records(day: datetime, values, start_hour=6):
    recs = []
    for i, tt in enumerate(values):
        recs.append({
            "epoch_start": day.replace(hour=start_hour) + timedelta(minutes=15 * i),
            "travel_time_sec": tt,
        })
    return recs


class TestLOTTR:
    def test_perfect_reliability(self):
        day = datetime(2025, 3, 4)  # Tuesday
        recs = _epoch_records(day, [300.0] * 16)  # flat AM peak
        out = compute_lottr(recs)
        assert out["am"] == 1.0
        assert out["reliable"] is True

    def test_unreliable_peak(self):
        day = datetime(2025, 3, 4)
        recs = _epoch_records(day, [300.0] * 12 + [600.0] * 4)
        out = compute_lottr(recs)
        assert out["am"] is not None and out["am"] > 1.0

    def test_insufficient_data_returns_none(self):
        assert compute_lottr([])["overall"] is None

    def test_weekend_bucket(self):
        sat = datetime(2025, 3, 8)  # Saturday
        recs = _epoch_records(sat, [280.0] * 8, start_hour=10)
        assert compute_lottr(recs)["weekend"] == 1.0


class TestTTTR:
    def test_stable_trucks(self):
        recs = [{"travel_time_sec": 400.0 + (i % 3)} for i in range(20)]
        out = compute_tttr(recs)
        assert out["tttr"] is not None and out["tttr"] < 1.5
        assert out["reliable"] is True

    def test_insufficient(self):
        assert compute_tttr([{"travel_time_sec": 1.0}])["tttr"] is None


class TestPHED:
    def test_no_delay_when_free_flow(self):
        recs = [{"speed_mph": 60.0, "volume_vph": 1500.0} for _ in range(4)]
        assert compute_phed(recs) == 0.0

    def test_positive_delay_when_congested(self):
        recs = [{"speed_mph": 10.0, "volume_vph": 2000.0} for _ in range(4)]
        assert compute_phed(recs) > 0.0
