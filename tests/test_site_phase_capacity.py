from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.site_phase_capacity import (
    STATUS_NOT_CONFIGURED,
    STATUS_OVER_LIMIT,
    STATUS_SOURCE_NOT_READY,
    STATUS_WITHIN_LIMIT,
    SitePhaseCapacitySettings,
    build_site_phase_capacity_status,
    update_site_phase_capacity_limit,
)

NOW = datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc)


class _States:
    def __init__(self, values):
        self.values = values

    def get(self, entity_id: str):
        value = self.values.get(entity_id)
        if value is None:
            return None
        state, unit, last_updated = value
        return SimpleNamespace(
            state=state,
            attributes={"unit_of_measurement": unit},
            last_updated=last_updated,
        )


class _Hass:
    def __init__(self, values):
        self.states = _States(values)


def _options(*, limit: float | None, phases=("L1", "L2", "L3")):
    role_by_phase = {
        "L1": "grid_current_l1",
        "L2": "grid_current_l2",
        "L3": "grid_current_l3",
    }
    options = {
        "entity_assignments": {
            "version": 1,
            "items": [
                {
                    "technology": "smart_meter",
                    "role": role_by_phase[phase],
                    "entity_id": f"sensor.current_{phase.lower()}",
                    "confirmed": True,
                }
                for phase in phases
            ],
        },
        "unrelated": {"preserve": True},
    }
    if limit is not None:
        options["site_phase_capacity"] = {"max_phase_current_a": limit}
    return options


def _hass(*, l1=("10", "A", NOW), l2=("20", "A", NOW), l3=("24", "A", NOW)):
    return _Hass(
        {
            "sensor.current_l1": l1,
            "sensor.current_l2": l2,
            "sensor.current_l3": l3,
        }
    )


def test_unconfigured_limit_keeps_currents_diagnostic_only() -> None:
    result = build_site_phase_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=None),
        now=NOW,
    )
    assert result.status == STATUS_NOT_CONFIGURED
    assert result.configured is False
    assert result.phases["L1"].current_a == pytest.approx(10.0)
    assert result.phases["L1"].headroom_a is None
    assert result.execution_guard_active is False
    assert result.service_call_performed is False
    assert result.execution_performed is False


def test_ready_sources_calculate_headroom_and_worst_phase() -> None:
    result = build_site_phase_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=25.0),
        now=NOW,
    )
    assert result.status == STATUS_WITHIN_LIMIT
    assert result.source_ready is True
    assert result.execution_guard_active is True
    assert result.phases["L1"].headroom_a == pytest.approx(15.0)
    assert result.phases["L2"].headroom_a == pytest.approx(5.0)
    assert result.phases["L3"].headroom_a == pytest.approx(1.0)
    assert result.phases["L3"].utilization_percent == pytest.approx(96.0)
    assert result.worst_phase == "L3"
    assert result.max_utilization_percent == pytest.approx(96.0)
    assert result.any_phase_over_limit is False


def test_one_phase_over_limit_is_reported_independently() -> None:
    result = build_site_phase_capacity_status(
        _hass(l2=("27.5", "A", NOW)),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=25.0),
        now=NOW,
    )
    assert result.status == STATUS_OVER_LIMIT
    assert result.execution_guard_active is True
    assert result.any_phase_over_limit is True
    assert result.phases["L2"].over_limit is True
    assert result.phases["L2"].over_limit_a == pytest.approx(2.5)
    assert result.phases["L2"].headroom_a == pytest.approx(0.0)
    assert result.worst_phase == "L2"


def test_partial_mapping_never_computes_false_headroom() -> None:
    result = build_site_phase_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=25.0, phases=("L1", "L2")),
        now=NOW,
    )
    assert result.status == STATUS_SOURCE_NOT_READY
    assert result.source_ready is False
    assert result.execution_guard_active is True
    assert result.phases["L1"].current_a == pytest.approx(10.0)
    assert result.phases["L1"].headroom_a is None
    assert result.phases["L3"].current_a is None
    assert result.worst_phase is None
    assert result.any_phase_over_limit is False


def test_stale_phase_never_computes_false_headroom() -> None:
    result = build_site_phase_capacity_status(
        _hass(l3=("24", "A", NOW - timedelta(seconds=301))),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=25.0),
        now=NOW,
    )
    assert result.status == STATUS_SOURCE_NOT_READY
    assert result.source_ready is False
    assert result.execution_guard_active is True
    assert all(item.headroom_a is None for item in result.phases.values())
    assert all(item.utilization_percent is None for item in result.phases.values())


@pytest.mark.parametrize("raw", [-1, 0, float("inf"), float("nan"), "bad", True])
def test_invalid_persisted_phase_limit_is_ignored(raw) -> None:
    options = _options(limit=None)
    options["site_phase_capacity"] = {"max_phase_current_a": raw}
    assert SitePhaseCapacitySettings.from_options(options).max_phase_current_a is None


def test_update_limit_preserves_unrelated_options_and_can_clear() -> None:
    original = _options(limit=None)
    updated = update_site_phase_capacity_limit(original, 25.0)
    assert updated["unrelated"] == {"preserve": True}
    assert updated["site_phase_capacity"]["max_phase_current_a"] == pytest.approx(25.0)
    assert "site_phase_capacity" not in original

    cleared = update_site_phase_capacity_limit(updated, None)
    assert cleared["site_phase_capacity"]["max_phase_current_a"] is None
