from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.site_capacity_sensor import (
    FrakonSiteCapacitySensor,
    SITE_CAPACITY_SENSOR_DEFINITIONS,
    build_site_capacity_sensors,
)


class _States:
    def __init__(self, values: dict[str, tuple[str, str]]) -> None:
        self.values = values

    def get(self, entity_id: str):
        value = self.values.get(entity_id)
        if value is None:
            return None
        state, unit = value
        return SimpleNamespace(state=state, attributes={"unit_of_measurement": unit})


class _Hass:
    def __init__(self, values: dict[str, tuple[str, str]]) -> None:
        self.states = _States(values)


def _entry(limit: float | None = 10.0):
    site_capacity = {} if limit is None else {"max_grid_import_kw": limit}
    return SimpleNamespace(
        entry_id="entry-1",
        options={
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
                "grid_meter_scope": "whole_house",
                "pv_power_scope": "gross_generation",
                "ev_wallbox_relation": "unknown",
            },
            "site_capacity": site_capacity,
        },
    )


def _hass(grid_import: str = "4"):
    return _Hass(
        {
            "sensor.pv": ("2", "kW"),
            "sensor.grid_in": (grid_import, "kW"),
            "sensor.grid_out": ("0", "kW"),
        }
    )


def _sensor(key: str, *, limit: float | None = 10.0, grid_import: str = "4") -> FrakonSiteCapacitySensor:
    definition = next(item for item in SITE_CAPACITY_SENSOR_DEFINITIONS if item.key == key)
    return FrakonSiteCapacitySensor(_hass(grid_import), _entry(limit), definition)  # type: ignore[arg-type]


def test_capacity_sensors_publish_limit_current_headroom_and_utilization() -> None:
    sensors = build_site_capacity_sensors(_hass(), _entry())  # type: ignore[arg-type]
    by_key = {sensor._definition.key: sensor for sensor in sensors}

    assert by_key["status"].native_value == "within_limit"
    assert by_key["grid_import"].native_value == pytest.approx(4.0)
    assert by_key["grid_limit"].native_value == pytest.approx(10.0)
    assert by_key["grid_headroom"].native_value == pytest.approx(6.0)
    assert by_key["grid_over_limit"].native_value == pytest.approx(0.0)
    assert by_key["grid_utilization"].native_value == pytest.approx(40.0)
    assert by_key["grid_utilization"].native_unit_of_measurement == "%"


def test_over_limit_sensor_reports_exact_excess_without_negative_headroom() -> None:
    headroom = _sensor("grid_headroom", grid_import="12.5")
    over = _sensor("grid_over_limit", grid_import="12.5")
    status = _sensor("status", grid_import="12.5")

    assert headroom.native_value == pytest.approx(0.0)
    assert over.native_value == pytest.approx(2.5)
    assert status.native_value == "over_limit"
    assert status.extra_state_attributes["execution_guard_active"] is False


def test_unconfigured_limit_keeps_status_available_but_limit_dependent_values_unavailable() -> None:
    status = _sensor("status", limit=None)
    limit = _sensor("grid_limit", limit=None)
    headroom = _sensor("grid_headroom", limit=None)

    assert status.available is True
    assert status.native_value == "not_configured"
    assert limit.available is False
    assert headroom.available is False
    assert status.extra_state_attributes["read_only"] is True


def test_unavailable_grid_source_never_fabricates_headroom() -> None:
    headroom = _sensor("grid_headroom", grid_import="unavailable")
    status = _sensor("status", grid_import="unavailable")

    assert headroom.native_value is None
    assert headroom.available is False
    assert status.native_value == "source_unavailable"
    assert status.extra_state_attributes["service_call_performed"] is False
