from types import SimpleNamespace

from custom_components.frakon_energy.ha_entity_registry_adapter import (
    registry_records_from_home_assistant,
)


def test_registry_adapter_preserves_metadata() -> None:
    entry = SimpleNamespace(
        entity_id="sensor.enyaq_battery",
        original_name="Battery level",
        name="Enyaq battery",
        platform="myskoda",
        device_id="device-1",
        device_class="battery",
        disabled_by=None,
        hidden_by=None,
    )
    state = SimpleNamespace(
        state="82",
        attributes={
            "state_class": "measurement",
            "unit_of_measurement": "%",
        },
    )

    records = registry_records_from_home_assistant(
        [entry],
        states={"sensor.enyaq_battery": state},
        device_names={"device-1": "Škoda Enyaq"},
    )

    assert len(records) == 1
    record = records[0]
    assert record.entity_id == "sensor.enyaq_battery"
    assert record.device_name == "Škoda Enyaq"
    assert record.platform == "myskoda"
    assert record.device_class == "battery"
    assert record.state_class == "measurement"
    assert record.unit_of_measurement == "%"
    assert record.unavailable is False


def test_registry_adapter_marks_missing_and_unavailable_states() -> None:
    missing = SimpleNamespace(
        entity_id="sensor.wallbox_power",
        original_name=None,
        name=None,
        platform="wallbox",
        device_id=None,
        device_class=None,
        disabled_by=None,
        hidden_by=None,
    )
    unavailable = SimpleNamespace(
        entity_id="sensor.pv_power",
        original_name=None,
        name=None,
        platform="inverter",
        device_id=None,
        device_class="power",
        disabled_by=None,
        hidden_by=None,
    )

    records = registry_records_from_home_assistant(
        [missing, unavailable],
        states={
            "sensor.pv_power": SimpleNamespace(state="unavailable", attributes={}),
        },
    )

    assert records[0].unavailable is True
    assert records[1].unavailable is True


def test_registry_adapter_ignores_invalid_entity_ids_and_marks_visibility() -> None:
    invalid = SimpleNamespace(entity_id="invalid")
    hidden = SimpleNamespace(
        entity_id="sensor.hidden",
        original_name=None,
        name=None,
        platform="demo",
        device_id=None,
        device_class=None,
        disabled_by="integration",
        hidden_by="user",
    )

    records = registry_records_from_home_assistant(
        [invalid, hidden],
        states={
            "sensor.hidden": SimpleNamespace(state="1", attributes={}),
        },
    )

    assert len(records) == 1
    assert records[0].disabled is True
    assert records[0].hidden is True
