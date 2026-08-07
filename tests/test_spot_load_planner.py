from datetime import datetime, timedelta, timezone

import pytest

from custom_components.frakon_energy.load_planner import (
    FlexibleLoadRequest as CoreLoadRequest,
    plan_contiguous_load,
)
from custom_components.frakon_energy.spot_load_planner import (
    FlexibleLoadRequest,
    plan_flexible_load,
)


def _series(prices: list[float]) -> list[dict[str, object]]:
    start = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "starts_at": (start + timedelta(minutes=15 * index)).isoformat(),
            "ends_at": (start + timedelta(minutes=15 * (index + 1))).isoformat(),
            "price_czk_kwh": price,
        }
        for index, price in enumerate(prices)
    ]


def test_legacy_adapter_matches_canonical_plan() -> None:
    intervals = _series([8, 8, 8, 8, 2, 1, 1, 2, 7, 7, 7, 7])
    canonical = plan_contiguous_load(
        intervals,
        CoreLoadRequest(load_id="Boiler", duration_minutes=60, power_kw=2),
    )
    legacy = plan_flexible_load(
        intervals,
        FlexibleLoadRequest(name="Boiler", duration_minutes=60, power_kw=2),
    )
    assert canonical is not None
    assert legacy is not None
    assert legacy["starts_at"] == canonical.starts_at.isoformat()
    assert legacy["ends_at"] == canonical.ends_at.isoformat()
    assert legacy["average_czk_kwh"] == pytest.approx(canonical.average_czk_kwh)
    assert legacy["minimum_czk_kwh"] == pytest.approx(canonical.minimum_czk_kwh)
    assert legacy["maximum_czk_kwh"] == pytest.approx(canonical.maximum_czk_kwh)
    assert legacy["estimated_energy_cost_czk"] == pytest.approx(canonical.estimated_cost_czk)


def test_legacy_adapter_validates_request() -> None:
    with pytest.raises(ValueError):
        plan_flexible_load([], FlexibleLoadRequest(name="", duration_minutes=60, power_kw=2))
