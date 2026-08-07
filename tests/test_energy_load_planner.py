from datetime import datetime

import pytest

from custom_components.frakon_energy.energy_load_planner import FlexibleLoad, plan_flexible_load
from custom_components.frakon_energy.spot_load_planner import (
    FlexibleLoadRequest,
    plan_flexible_load as plan_spot_load,
)


def _interval(index: int, price: float) -> dict[str, object]:
    hour, minute = divmod(index * 15, 60)
    end_hour, end_minute = divmod((index + 1) * 15, 60)
    return {
        "starts_at": f"2026-08-08T{hour:02d}:{minute:02d}:00+02:00",
        "ends_at": f"2026-08-08T{end_hour:02d}:{end_minute:02d}:00+02:00",
        "price_czk_kwh": price,
    }


def test_plans_cheapest_contiguous_run_and_estimates_cost() -> None:
    intervals = [_interval(i, 8.0) for i in range(24)]
    for i in range(8, 16):
        intervals[i] = _interval(i, 2.0)
    load = FlexibleLoad("ev", "EV", duration_minutes=120, power_kw=11.0)
    plan = plan_flexible_load(intervals, load)
    assert plan is not None
    assert plan.starts_at == intervals[8]["starts_at"]
    assert plan.ends_at == intervals[15]["ends_at"]
    assert plan.interval_count == 8
    assert plan.minimum_czk_kwh == pytest.approx(2.0)
    assert plan.maximum_czk_kwh == pytest.approx(2.0)
    assert plan.estimated_energy_kwh == pytest.approx(22.0)
    assert plan.estimated_cost_czk == pytest.approx(44.0)


def test_respects_earliest_start_and_deadline() -> None:
    intervals = [_interval(i, 1.0 if i < 8 else 3.0) for i in range(16)]
    load = FlexibleLoad(
        "boiler", "Bojler", duration_minutes=60, power_kw=2.0,
        earliest_start=datetime.fromisoformat("2026-08-08T02:00:00+02:00"),
        deadline=datetime.fromisoformat("2026-08-08T04:00:00+02:00"),
    )
    plan = plan_flexible_load(intervals, load)
    assert plan is not None
    assert plan.starts_at == "2026-08-08T02:00:00+02:00"


def test_rejects_non_quarter_hour_duration() -> None:
    with pytest.raises(ValueError):
        plan_flexible_load([_interval(0, 1.0)], FlexibleLoad("x", "X", 20, 1.0))


def test_rejects_invalid_time_window() -> None:
    instant = datetime.fromisoformat("2026-08-08T02:00:00+02:00")
    with pytest.raises(ValueError):
        plan_flexible_load(
            [_interval(0, 1.0)],
            FlexibleLoad("x", "X", 15, 1.0, earliest_start=instant, deadline=instant),
        )


def test_legacy_spot_adapter_uses_same_plan_as_canonical_engine() -> None:
    intervals = [_interval(i, 7.0) for i in range(20)]
    prices = [1.5, 2.0, 2.5, 3.0]
    for offset, price in enumerate(prices, start=8):
        intervals[offset] = _interval(offset, price)

    canonical = plan_flexible_load(
        intervals,
        FlexibleLoad("boiler", "Boiler", duration_minutes=60, power_kw=2.0),
    )
    legacy = plan_spot_load(
        intervals,
        FlexibleLoadRequest(name="Boiler", duration_minutes=60, power_kw=2.0),
    )

    assert canonical is not None
    assert legacy is not None
    assert legacy["starts_at"] == canonical.starts_at
    assert legacy["ends_at"] == canonical.ends_at
    assert legacy["average_czk_kwh"] == pytest.approx(canonical.average_czk_kwh)
    assert legacy["minimum_czk_kwh"] == pytest.approx(canonical.minimum_czk_kwh)
    assert legacy["maximum_czk_kwh"] == pytest.approx(canonical.maximum_czk_kwh)
    assert legacy["estimated_energy_cost_czk"] == pytest.approx(canonical.estimated_cost_czk)
