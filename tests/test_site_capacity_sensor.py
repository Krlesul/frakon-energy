from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.site_capacity_sensor import (
    FrakonSiteCapacitySensor,
    build_site_capacity_sensors,
    site_capacity_sensor_definitions,
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
        return SimpleNamespace(
            state=state,
            attributes={"unit_of_measurement": unit},
            last_updated=datetime.now(timezone.utc),
        )


class _Hass:
    def __init__(self, grid_state: str = "5") -> None:
        self.states = _States(grid_state)


def _entry(
    limit: float | None,
    *,
    grid_scope: str = "whole_house",
    guard_enabled: bool | None = None,
):
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
    if limit is not None:
        capacity: dict[str, object] = {"max_grid_import_kw": limit}
        if guard_enabled is not None:
            capacity["execution_guard_enabled"] = guard_enabled
        options["site_capacity"] = capacity
    return SimpleNamespace(entry_id="entry-1", options=options)


def test_capacity_sensors_exist_only_when_limit_is_configured() -> None:
    assert site_capacity_sensor_definitions(_entry(None)) == ()
    assert {item.key for item in site_capacity_sensor_definitions(_entry(12.0))} == {
        "grid_capacity_status",
        "grid_import_headroom_power",
        "grid_import_over_limit_power",
    }


def test_capacity_status_and_numeric_sensors_share_same_model() -> None:
    entry = _entry(12.0)
    hass = _Hass()
    sensors = build_site_capacity_sensors(hass, entry)  # type: ignore[arg-type]
    by_key = {sensor._definition.key: sensor for sensor in sensors}

    assert by_key["grid_capacity_status"].native_value == "within_limit"
    assert by_key["grid_capacity_status"].available is True
    assert by_key["grid_import_headroom_power"].native_value == pytest.approx(7.0)
    assert by_key["grid_import_headroom_power"].available is True
    assert by_key["grid_import_over_limit_power"].native_value == pytest.approx(0.0)
    assert by_key["grid_import_over_limit_power"].available is True
    assert by_key["grid_import_headroom_power"].native_unit_of_measurement == "kW"
    assert by_key["grid_import_headroom_power"].device_class == "power"
    assert by_key["grid_capacity_status"].extra_state_attributes["execution_guard_active"] is True
    assert by_key["grid_capacity_status"].extra_state_attributes["source_fresh"] is True


def test_explicit_diagnostic_only_limit_is_visible_on_sensor_attributes() -> None:
    entry = _entry(12.0, guard_enabled=False)
    hass = _Hass()
    definition = next(
        item for item in site_capacity_sensor_definitions(entry) if item.key == "grid_capacity_status"
    )
    sensor = FrakonSiteCapacitySensor(hass, entry, definition)  # type: ignore[arg-type]

    assert sensor.native_value == "within_limit"
    assert sensor.extra_state_attributes["execution_guard_active"] is False
    assert sensor.extra_state_attributes["grid_headroom_kw"] == pytest.approx(7.0)


def test_status_stays_available_when_topology_is_wrong_but_numeric_sensors_do_not() -> None:
    entry = _entry(12.0, grid_scope="inverter_branch")
    hass = _Hass()
    sensors = build_site_capacity_sensors(hass, entry)  # type: ignore[arg-type]
    by_key = {sensor._definition.key: sensor for sensor in sensors}

    assert by_key["grid_capacity_status"].native_value == "topology_not_ready"
    assert by_key["grid_capacity_status"].available is True
    assert by_key["grid_import_headroom_power"].native_value is None
    assert by_key["grid_import_headroom_power"].available is False
    assert by_key["grid_import_over_limit_power"].native_value is None
    assert by_key["grid_import_over_limit_power"].available is False


def test_status_stays_available_when_grid_source_is_unavailable() -> None:
    entry = _entry(12.0)
    hass = _Hass("unavailable")
    definition = next(
        item for item in site_capacity_sensor_definitions(entry) if item.key == "grid_capacity_status"
    )
    sensor = FrakonSiteCapacitySensor(hass, entry, definition)  # type: ignore[arg-type]

    assert sensor.native_value == "source_unavailable"
    assert sensor.available is True
    assert sensor.extra_state_attributes["grid_headroom_kw"] is None
    assert sensor.extra_state_attributes["execution_guard_active"] is True
    assert sensor.extra_state_attributes["source_entity_id"] == "sensor.grid_in"