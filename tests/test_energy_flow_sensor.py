from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frakon_energy.energy_flow_sensor import (
    FrakonEnergyFlowSensor,
    build_energy_flow_sensors,
    energy_flow_sensor_definitions,
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


def _entry(*, battery: bool = False, wallbox: bool = False):
    technologies = [
        {"id": "photovoltaics", "enabled": True, "entity_ids": []},
        {"id": "smart_meter", "enabled": True, "entity_ids": []},
    ]
    assignments = [
        {"technology": "photovoltaics", "role": "pv_power", "entity_id": "sensor.pv", "confirmed": True},
        {"technology": "smart_meter", "role": "grid_import", "entity_id": "sensor.grid_in", "confirmed": True},
        {"technology": "smart_meter", "role": "grid_export", "entity_id": "sensor.grid_out", "confirmed": True},
    ]
    if battery:
        technologies.append({"id": "home_battery", "enabled": True, "entity_ids": []})
        assignments.append(
            {"technology": "home_battery", "role": "power", "entity_id": "sensor.battery", "confirmed": True}
        )
    if wallbox:
        technologies.append({"id": "wallbox", "enabled": True, "entity_ids": []})
        assignments.append(
            {"technology": "wallbox", "role": "power", "entity_id": "sensor.wallbox", "confirmed": True}
        )
    return SimpleNamespace(
        entry_id="entry-1",
        options={
            "technologies": technologies,
            "entity_assignments": {"version": 1, "items": assignments},
            "energy_flow": {
                "battery_power_sign": "positive_is_charge" if battery else "unknown",
                "grid_meter_scope": "whole_house",
                "pv_power_scope": "gross_generation",
                "ev_wallbox_relation": "unknown",
            },
        },
    )


def _hass(*, battery: bool = False, wallbox: bool = False):
    values = {
        "sensor.pv": ("3", "kW"),
        "sensor.grid_in": ("1", "kW"),
        "sensor.grid_out": ("0.5", "kW"),
    }
    if battery:
        values["sensor.battery"] = ("1", "kW")
    if wallbox:
        values["sensor.wallbox"] = ("7.4", "kW")
    return _Hass(values)


def test_sensor_definitions_only_add_optional_entities_for_enabled_technologies() -> None:
    base_keys = {item.key for item in energy_flow_sensor_definitions(_entry())}
    assert base_keys == {
        "house_load_power",
        "pv_generation_power",
        "grid_import_power",
        "grid_export_power",
        "quality",
    }

    extended_keys = {
        item.key for item in energy_flow_sensor_definitions(_entry(battery=True, wallbox=True))
    }
    assert {"battery_charge_power", "battery_discharge_power", "known_load_power"} <= extended_keys


def test_house_load_sensor_uses_authoritative_model_and_exposes_formula() -> None:
    entry = _entry()
    hass = _hass()
    definitions = energy_flow_sensor_definitions(entry)
    definition = next(item for item in definitions if item.key == "house_load_power")
    sensor = FrakonEnergyFlowSensor(hass, entry, definition)  # type: ignore[arg-type]

    assert sensor.native_value == pytest.approx(3.5)
    assert sensor.available is True
    assert sensor.native_unit_of_measurement == "kW"
    assert sensor.device_class == "power"
    assert sensor.state_class == "measurement"
    assert sensor.extra_state_attributes["quality"] == "complete"
    assert sensor.extra_state_attributes["formula"].startswith("PV + grid import")


def test_quality_sensor_remains_available_when_topology_is_not_ready() -> None:
    entry = _entry()
    entry.options["energy_flow"]["grid_meter_scope"] = "unknown"
    hass = _hass()
    definition = next(
        item for item in energy_flow_sensor_definitions(entry) if item.key == "quality"
    )
    sensor = FrakonEnergyFlowSensor(hass, entry, definition)  # type: ignore[arg-type]

    assert sensor.available is True
    assert sensor.native_value == "needs_setup"
    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None
    assert sensor.extra_state_attributes["house_load_kw"] is None
    assert sensor.extra_state_attributes["source_entities"]["pv"] == "sensor.pv"


def test_battery_and_known_load_sensors_follow_enabled_profile() -> None:
    entry = _entry(battery=True, wallbox=True)
    hass = _hass(battery=True, wallbox=True)
    sensors = build_energy_flow_sensors(hass, entry)  # type: ignore[arg-type]
    by_key = {sensor._definition.key: sensor for sensor in sensors}

    assert by_key["battery_charge_power"].native_value == pytest.approx(1.0)
    assert by_key["battery_discharge_power"].native_value == pytest.approx(0.0)
    assert by_key["known_load_power"].native_value == pytest.approx(7.4)
    assert by_key["known_load_power"].available is True


def test_power_sensor_becomes_unavailable_instead_of_fabricating_value() -> None:
    entry = _entry()
    hass = _hass()
    hass.states.values["sensor.pv"] = ("unavailable", "kW")
    definition = next(
        item for item in energy_flow_sensor_definitions(entry) if item.key == "house_load_power"
    )
    sensor = FrakonEnergyFlowSensor(hass, entry, definition)  # type: ignore[arg-type]

    assert sensor.native_value is None
    assert sensor.available is False
    assert sensor.extra_state_attributes["quality"] == "partial"


def test_source_entity_subscription_scope_contains_only_confirmed_model_inputs() -> None:
    entry = _entry(battery=True, wallbox=True)
    hass = _hass(battery=True, wallbox=True)
    definition = next(
        item for item in energy_flow_sensor_definitions(entry) if item.key == "quality"
    )
    sensor = FrakonEnergyFlowSensor(hass, entry, definition)  # type: ignore[arg-type]

    assert set(sensor._source_entity_ids()) == {
        "sensor.pv",
        "sensor.grid_in",
        "sensor.grid_out",
        "sensor.battery",
        "sensor.wallbox",
    }
