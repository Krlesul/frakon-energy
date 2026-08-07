from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.load_planner import FlexibleLoadRequest, plan_contiguous_load


def _series(prices: list[float], *, start: datetime | None = None) -> list[dict[str, object]]:
    start = start or datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    result: list[dict[str, object]] = []
    for index, price in enumerate(prices):
        interval_start = start + timedelta(minutes=15 * index)
        result.append({
            "starts_at": interval_start.isoformat(),
            "ends_at": (interval_start + timedelta(minutes=15)).isoformat(),
            "price_czk_kwh": price,
        })
    return result


def test_plans_cheapest_contiguous_hour() -> None:
    intervals = _series([8, 8, 8, 8, 2, 1, 1, 2, 7, 7, 7, 7])
    result = plan_contiguous_load(intervals, FlexibleLoadRequest(load_id="boiler", duration_minutes=60))
    assert result is not None
    assert result.starts_at == datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)
    assert result.average_czk_kwh == pytest.approx(1.5)


def test_respects_earliest_start_and_deadline() -> None:
    start = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    intervals = _series([1, 1, 1, 1, 5, 5, 5, 5, 2, 2, 2, 2], start=start)
    result = plan_contiguous_load(
        intervals,
        FlexibleLoadRequest(
            load_id="ev",
            duration_minutes=60,
            earliest_start=start + timedelta(hours=1),
            latest_end=start + timedelta(hours=3),
        ),
    )
    assert result is not None
    assert result.starts_at == start + timedelta(hours=2)


def test_estimates_energy_and_cost_when_power_is_known() -> None:
    intervals = _series([4, 4, 4, 4])
    result = plan_contiguous_load(
        intervals,
        FlexibleLoadRequest(load_id="ev", duration_minutes=60, power_kw=11),
    )
    assert result is not None
    assert result.estimated_energy_kwh == pytest.approx(11)
    assert result.estimated_cost_czk == pytest.approx(44)


def test_gap_breaks_contiguous_window() -> None:
    intervals = _series([1, 1, 1, 1])
    intervals[2]["starts_at"] = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc).isoformat()
    intervals[2]["ends_at"] = datetime(2026, 8, 8, 1, 15, tzinfo=timezone.utc).isoformat()
    intervals[3]["starts_at"] = datetime(2026, 8, 8, 1, 15, tzinfo=timezone.utc).isoformat()
    intervals[3]["ends_at"] = datetime(2026, 8, 8, 1, 30, tzinfo=timezone.utc).isoformat()
    assert plan_contiguous_load(intervals, FlexibleLoadRequest(load_id="x", duration_minutes=60)) is None


def test_invalid_request_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_contiguous_load([], FlexibleLoadRequest(load_id="", duration_minutes=0))
