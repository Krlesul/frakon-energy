from datetime import datetime

import pytest

from custom_components.frakon_energy.spot_load_planner import FlexibleLoadRequest, plan_flexible_load


def _interval(index: int, price: float) -> dict[str, object]:
    hour, minute = divmod(index * 15, 60)
    end_hour, end_minute = divmod((index + 1) * 15, 60)
    return {
        "starts_at": f"2026-08-07T{hour:02d}:{minute:02d}:00+02:00",
        "ends_at": f"2026-08-07T{end_hour:02d}:{end_minute:02d}:00+02:00",
        "price_czk_kwh": price,
    }


def test_plans_cheapest_two_hour_load() -> None:
    intervals = [_interval(index, 7.0) for index in range(24)]
    for index in range(8, 16):
        intervals[index] = _interval(index, 2.0)
    request = FlexibleLoadRequest(name="EV", duration_minutes=120, power_kw=11)
    plan = plan_flexible_load(intervals, request)
    assert plan is not None
    assert plan["starts_at"] == "2026-08-07T02:00:00+02:00"
    assert plan["ends_at"] == "2026-08-07T04:00:00+02:00"
    assert plan["energy_kwh"] == pytest.approx(22)
    assert plan["estimated_energy_cost_czk"] == pytest.approx(44)


def test_respects_allowed_time_window() -> None:
    intervals = [_interval(index, 1.0 if index < 8 else 3.0) for index in range(24)]
    request = FlexibleLoadRequest(
        name="Boiler",
        duration_minutes=60,
        power_kw=2,
        earliest_start=datetime.fromisoformat("2026-08-07T03:00:00+02:00"),
        latest_end=datetime.fromisoformat("2026-08-07T06:00:00+02:00"),
    )
    plan = plan_flexible_load(intervals, request)
    assert plan is not None
    assert plan["starts_at"] >= "2026-08-07T03:00:00+02:00"
    assert plan["ends_at"] <= "2026-08-07T06:00:00+02:00"


def test_requires_quarter_hour_duration() -> None:
    with pytest.raises(ValueError):
        plan_flexible_load([], FlexibleLoadRequest(name="EV", duration_minutes=20, power_kw=11))
