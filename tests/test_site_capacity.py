from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.site_capacity import (
    DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS,
    STATUS_NOT_CONFIGURED,
    STATUS_OVER_LIMIT,
    STATUS_SOURCE_STALE,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_TOPOLOGY_NOT_READY,
    STATUS_WITHIN_LIMIT,
    SiteCapacitySettings,
    build_site_capacity_status,
    update_site_capacity_limit,
)

NOW = datetime(2026, 8, 8, 20, 45, tzinfo=timezone.utc)


class _States:
    def __init__(self, values: dict[str, tuple[str, str, datetime]]) -> None:
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
    def __init__(self, values: dict[str, tuple[str, str, datetime]]) -> None:
        self.states = _States(values)


def _options(*, limit: float | None, grid_scope: str = "whole_house") -> dict:
    options = {
        "technologies": [
            {"id": "photovoltaics", "enabled": True, "entity_ids": []},
            {"id": "smart_meter", "enabled": True, "entity_ids": []},
        ],
        "entity_assignments": {
            "version": 1,
            "items": [
                {"technology": "photovoltaics", "role": "pv_power", "entity_id": "sensor.pv", "confirmed": True},
                {"technology": "smart_meter", "role": "grid_import", "entity_id": "sensor.grid_in", "confirmed": True},
                {"technology": "smart_meter", "role": "grid_export", "entity_id": "sensor.grid_out", "confirmed": True},
            ],
        },
        "energy_flow": {
            "battery_power_sign": "unknown",
            "grid_meter_scope": grid_scope,
            "pv_power_scope": "gross_generation",
            "ev_wallbox_relation": "unknown",
        },
        "unrelated": {"preserve": True},
    }
    if limit is not None:
        options["site_capacity"] = {"max_grid_import_kw": limit}
    return options


def _hass(
    grid_state: str = "5",
    grid_unit: str = "kW",
    *,
    grid_age_seconds: int = 0,
) -> _Hass:
    fresh = NOW
    return _Hass(
        {
            "sensor.pv": ("3", "kW", fresh),
            "sensor.grid_in": (
                grid_state,
                grid_unit,
                NOW - timedelta(seconds=grid_age_seconds),
            ),
            "sensor.grid_out": ("0.5", "kW", fresh),
        }
    )


def test_capacity_not_configured_is_read_only_and_does_not_degrade_flow() -> None:
    result = build_site_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=None),
        now=NOW,
    )

    assert result.status == STATUS_NOT_CONFIGURED
    assert result.configured is False
    assert result.current_grid_import_kw == pytest.approx(5.0)
    assert result.source_fresh is True
    assert result.source_age_seconds == pytest.approx(0.0)
    assert result.grid_headroom_kw is None
    assert result.grid_over_limit_kw is None
    assert result.execution_guard_active is False
    assert result.service_call_performed is False
    assert result.execution_performed is False


def test_whole_house_meter_calculates_positive_headroom() -> None:
    result = build_site_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=12.0),
        now=NOW,
    )

    assert result.status == STATUS_WITHIN_LIMIT
    assert result.topology_ready is True
    assert result.source_available is True
    assert result.source_fresh is True
    assert result.max_grid_import_kw == pytest.approx(12.0)
    assert result.current_grid_import_kw == pytest.approx(5.0)
    assert result.grid_headroom_kw == pytest.approx(7.0)
    assert result.grid_over_limit_kw == pytest.approx(0.0)
    assert result.utilization_percent == pytest.approx(100 * 5 / 12)


def test_stale_grid_source_blocks_headroom_even_when_numeric_value_exists() -> None:
    result = build_site_capacity_status(
        _hass(grid_age_seconds=DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS + 1),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=12.0),
        now=NOW,
    )

    assert result.status == STATUS_SOURCE_STALE
    assert result.source_available is True
    assert result.source_fresh is False
    assert result.source_age_seconds == pytest.approx(
        DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS + 1
    )
    assert result.current_grid_import_kw == pytest.approx(5.0)
    assert result.grid_headroom_kw is None
    assert result.grid_over_limit_kw is None
    assert str(DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS) in result.reason


def test_source_at_exact_max_age_is_still_fresh() -> None:
    result = build_site_capacity_status(
        _hass(grid_age_seconds=DEFAULT_CAPACITY_SOURCE_MAX_AGE_SECONDS),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=12.0),
        now=NOW,
    )

    assert result.status == STATUS_WITHIN_LIMIT
    assert result.source_fresh is True


def test_over_limit_reports_excess_without_negative_headroom() -> None:
    result = build_site_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=4.0),
        now=NOW,
    )

    assert result.status == STATUS_OVER_LIMIT
    assert result.grid_headroom_kw == pytest.approx(0.0)
    assert result.grid_over_limit_kw == pytest.approx(1.0)
    assert result.utilization_percent == pytest.approx(125.0)


def test_inverter_branch_meter_cannot_be_used_as_site_capacity_guard() -> None:
    result = build_site_capacity_status(
        _hass(),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=12.0, grid_scope="inverter_branch"),
        now=NOW,
    )

    assert result.status == STATUS_TOPOLOGY_NOT_READY
    assert result.topology_ready is False
    assert result.current_grid_import_kw == pytest.approx(5.0)
    assert result.grid_headroom_kw is None
    assert result.grid_over_limit_kw is None


def test_unavailable_grid_source_cannot_create_fake_headroom() -> None:
    result = build_site_capacity_status(
        _hass("unavailable"),  # type: ignore[arg-type]
        entry_id="entry-1",
        options=_options(limit=12.0),
        now=NOW,
    )

    assert result.status == STATUS_SOURCE_UNAVAILABLE
    assert result.source_available is False
    assert result.current_grid_import_kw is None
    assert result.grid_headroom_kw is None
    assert result.grid_over_limit_kw is None


@pytest.mark.parametrize("raw", [-1, 0, float("inf"), float("nan"), "bad", True])
def test_invalid_persisted_limit_is_ignored_fail_closed(raw) -> None:
    options = _options(limit=None)
    options["site_capacity"] = {"max_grid_import_kw": raw}

    assert SiteCapacitySettings.from_options(options).max_grid_import_kw is None


def test_update_limit_preserves_unrelated_options_and_can_clear_limit() -> None:
    original = _options(limit=None)
    updated = update_site_capacity_limit(original, 17.0)

    assert updated["unrelated"] == {"preserve": True}
    assert updated["site_capacity"]["max_grid_import_kw"] == pytest.approx(17.0)
    assert "site_capacity" not in original

    cleared = update_site_capacity_limit(updated, None)
    assert cleared["site_capacity"]["max_grid_import_kw"] is None
