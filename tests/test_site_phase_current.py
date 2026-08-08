from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.site_phase_current import (
    STATUS_NOT_CONFIGURED,
    STATUS_PARTIAL,
    STATUS_READY,
    STATUS_SOURCE_STALE,
    STATUS_SOURCE_UNAVAILABLE,
    build_site_phase_current_status,
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


def _options(*phases: str):
    role_by_phase = {
        "L1": "grid_current_l1",
        "L2": "grid_current_l2",
        "L3": "grid_current_l3",
    }
    return {
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
        }
    }


def _hass(*, l1=("10", "A", NOW), l2=("11500", "mA", NOW), l3=("12", "A", NOW)):
    return _Hass(
        {
            "sensor.current_l1": l1,
            "sensor.current_l2": l2,
            "sensor.current_l3": l3,
        }
    )


def test_no_phase_assignments_is_not_configured() -> None:
    result = build_site_phase_current_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(),
        now=NOW,
    )
    assert result.status == STATUS_NOT_CONFIGURED
    assert result.configured_phases == 0
    assert result.mapping_complete is False
    assert result.execution_guard_active is False


def test_partial_mapping_never_infers_missing_phase() -> None:
    result = build_site_phase_current_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options("L1", "L2"),
        now=NOW,
    )
    assert result.status == STATUS_PARTIAL
    assert result.configured_phases == 2
    assert result.phases["L3"].entity_id is None
    assert result.phases["L3"].current_a is None
    assert result.mapping_complete is False


def test_three_fresh_confirmed_phase_currents_are_ready_and_convert_ma() -> None:
    result = build_site_phase_current_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options("L1", "L2", "L3"),
        now=NOW,
    )
    assert result.status == STATUS_READY
    assert result.mapping_complete is True
    assert result.all_sources_available is True
    assert result.all_sources_fresh is True
    assert result.phases["L1"].current_a == pytest.approx(10.0)
    assert result.phases["L2"].current_a == pytest.approx(11.5)
    assert result.phases["L3"].current_a == pytest.approx(12.0)
    assert result.read_only is True
    assert result.service_call_performed is False


def test_unsupported_unit_is_unavailable_not_guessed() -> None:
    result = build_site_phase_current_status(
        _hass(l2=("11.5", "kW", NOW)),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options("L1", "L2", "L3"),
        now=NOW,
    )
    assert result.status == STATUS_SOURCE_UNAVAILABLE
    assert result.phases["L2"].current_a is None
    assert result.phases["L2"].reason == "unsupported_unit:kW"


def test_one_stale_phase_makes_whole_snapshot_stale() -> None:
    result = build_site_phase_current_status(
        _hass(l3=("12", "A", NOW - timedelta(seconds=301))),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options("L1", "L2", "L3"),
        now=NOW,
    )
    assert result.status == STATUS_SOURCE_STALE
    assert result.all_sources_available is True
    assert result.all_sources_fresh is False
    assert result.phases["L3"].source_fresh is False
    assert result.phases["L3"].current_a == pytest.approx(12.0)


def test_negative_current_is_rejected_instead_of_taking_absolute_value() -> None:
    result = build_site_phase_current_status(
        _hass(l1=("-1", "A", NOW)),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options("L1", "L2", "L3"),
        now=NOW,
    )
    assert result.status == STATUS_SOURCE_UNAVAILABLE
    assert result.phases["L1"].reason == "negative_current_not_supported"