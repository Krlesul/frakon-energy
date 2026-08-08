from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.load_execution_capacity_guard import (
    GUARD_BLOCKED,
    GUARD_DISABLED,
    GUARD_READY,
    REASON_ALREADY_OVER_LIMIT,
    REASON_DISABLED,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_LIMIT_MISSING,
    REASON_READY,
    REASON_SOURCE_UNAVAILABLE,
    REASON_TOPOLOGY_NOT_READY,
    evaluate_site_capacity_start,
)
from custom_components.frakon_energy.site_capacity import (
    SiteCapacitySettings,
    update_site_capacity_guard,
    update_site_capacity_limit,
)


class _States:
    def __init__(self, grid_state: str = "5") -> None:
        self.grid_state = grid_state

    def get(self, entity_id: str):
        values = {
            "sensor.pv": ("3", "kW"),
            "sensor.grid_in": (self.grid_state, "kW"),
            "sensor.grid_out": ("0.5", "kW"),
        }
        value = values.get(entity_id)
        if value is None:
            return None
        state, unit = value
        return SimpleNamespace(state=state, attributes={"unit_of_measurement": unit})


class _ConfigEntries:
    def __init__(self, entry) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str):
        return self.entry if entry_id == self.entry.entry_id else None


class _Hass:
    def __init__(self, options: dict, grid_state: str = "5") -> None:
        self.states = _States(grid_state)
        self.config_entries = _ConfigEntries(
            SimpleNamespace(entry_id="entry-1", domain="frakon_energy", options=options)
        )
        # Intentionally no services registry: guard evaluation must stay read-only.


def _options(
    *,
    limit: float | None = 12.0,
    guard: bool = False,
    grid_scope: str = "whole_house",
) -> dict:
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
    }
    if limit is not None or guard:
        options["site_capacity"] = {
            "max_grid_import_kw": limit,
            "execution_guard_enabled": guard,
        }
    return options


def test_guard_disabled_preserves_existing_start_behavior() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(guard=False)),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=11.0,
    )

    assert result.status == GUARD_DISABLED
    assert result.reason == REASON_DISABLED
    assert result.guard_applies is False
    assert result.can_start is True
    assert result.projected_grid_import_kw == pytest.approx(16.0)
    assert result.service_call_performed is False
    assert result.execution_performed is False


def test_enabled_guard_allows_load_inside_headroom() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(limit=12.0, guard=True), grid_state="5"),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=6.0,
    )

    assert result.status == GUARD_READY
    assert result.reason == REASON_READY
    assert result.can_start is True
    assert result.projected_grid_import_kw == pytest.approx(11.0)
    assert result.headroom_before_kw == pytest.approx(7.0)
    assert result.headroom_after_kw == pytest.approx(1.0)


def test_enabled_guard_blocks_load_that_would_cross_limit() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(limit=12.0, guard=True), grid_state="5"),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=11.0,
    )

    assert result.status == GUARD_BLOCKED
    assert result.reason == REASON_INSUFFICIENT_HEADROOM
    assert result.can_start is False
    assert result.projected_grid_import_kw == pytest.approx(16.0)
    assert result.projected_over_limit_kw == pytest.approx(4.0)
    assert result.headroom_after_kw == pytest.approx(0.0)


def test_enabled_guard_blocks_when_house_is_already_over_limit() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(limit=12.0, guard=True), grid_state="13"),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=1.0,
    )
    assert result.status == GUARD_BLOCKED
    assert result.reason == REASON_ALREADY_OVER_LIMIT
    assert result.can_start is False


def test_enabled_guard_blocks_wrong_meter_topology() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(limit=12.0, guard=True, grid_scope="inverter_branch")),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=1.0,
    )
    assert result.status == GUARD_BLOCKED
    assert result.reason == REASON_TOPOLOGY_NOT_READY
    assert result.can_start is False


def test_enabled_guard_blocks_unavailable_grid_source() -> None:
    result = evaluate_site_capacity_start(
        _Hass(_options(limit=12.0, guard=True), grid_state="unavailable"),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=1.0,
    )
    assert result.status == GUARD_BLOCKED
    assert result.reason == REASON_SOURCE_UNAVAILABLE
    assert result.can_start is False


def test_corrupt_guard_enabled_without_limit_blocks_fail_closed() -> None:
    options = _options(limit=None, guard=True)
    result = evaluate_site_capacity_start(
        _Hass(options),  # type: ignore[arg-type]
        entry_id="entry-1",
        additional_power_kw=1.0,
    )
    assert SiteCapacitySettings.from_options(options).execution_guard_enabled is True
    assert result.status == GUARD_BLOCKED
    assert result.reason == REASON_LIMIT_MISSING
    assert result.can_start is False


def test_guard_cannot_be_enabled_without_limit() -> None:
    with pytest.raises(ValueError, match="requires max_grid_import_kw"):
        update_site_capacity_guard(_options(limit=None, guard=False), True)


def test_limit_cannot_be_cleared_until_guard_is_disabled() -> None:
    options = _options(limit=12.0, guard=True)
    with pytest.raises(ValueError, match="disable site capacity execution guard"):
        update_site_capacity_limit(options, None)

    disabled = update_site_capacity_guard(options, False)
    cleared = update_site_capacity_limit(disabled, None)
    settings = SiteCapacitySettings.from_options(cleared)
    assert settings.execution_guard_enabled is False
    assert settings.max_grid_import_kw is None
