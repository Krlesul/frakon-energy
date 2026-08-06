from datetime import datetime, time

from custom_components.frakon_energy.hdo_timeline import (
    TimelineInterval,
    build_timeline_snapshot,
)


def test_live_marker_and_hourly_labels() -> None:
    snapshot = build_timeline_snapshot(
        now=datetime(2026, 8, 6, 13, 42),
        intervals=(TimelineInterval(time(13, 10), time(15, 25)),),
    )
    assert snapshot.current_time_label == "13:42"
    assert snapshot.current_low_tariff is True
    assert round(snapshot.current_position_percent, 2) == 57.08
    assert len(snapshot.desktop_markers) == 25
    assert snapshot.desktop_markers[1].label == "01"
    assert len(snapshot.compact_markers) == 13


def test_outside_low_tariff_interval() -> None:
    snapshot = build_timeline_snapshot(
        now=datetime(2026, 8, 6, 18, 0),
        intervals=(TimelineInterval(time(13, 10), time(15, 25)),),
    )
    assert snapshot.current_low_tariff is False
    assert snapshot.intervals[0]["start"] == "13:10"
    assert snapshot.intervals[0]["end"] == "15:25"
