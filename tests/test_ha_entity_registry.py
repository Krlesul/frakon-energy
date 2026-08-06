from custom_components.frakon_energy.ha_entity_registry import (
    RegistryEntityRecord,
    discovery_descriptors_from_registry,
    registry_record_from_mapping,
)


def test_registry_mapping_uses_entity_domain_and_metadata() -> None:
    record = registry_record_from_mapping(
        {
            "entity_id": "sensor.enyaq_battery",
            "name": "Stav baterie Enyaq",
            "device_name": "Škoda Enyaq",
            "platform": "myskoda",
            "device_class": "battery",
            "unit_of_measurement": "%",
        }
    )

    assert record.domain == "sensor"
    assert record.platform == "myskoda"
    assert record.unit_of_measurement == "%"


def test_registry_adapter_excludes_disabled_hidden_and_unavailable_entities() -> None:
    descriptors = discovery_descriptors_from_registry(
        (
            RegistryEntityRecord("sensor.visible", name="Visible"),
            RegistryEntityRecord("sensor.disabled", disabled=True),
            RegistryEntityRecord("sensor.hidden", hidden=True),
            RegistryEntityRecord("sensor.unavailable", unavailable=True),
        )
    )

    assert [item.entity_id for item in descriptors] == ["sensor.visible"]


def test_registry_adapter_can_include_temporarily_unavailable_entities() -> None:
    descriptors = discovery_descriptors_from_registry(
        (RegistryEntityRecord("sensor.enyaq_range", unavailable=True),),
        include_unavailable=True,
    )

    assert descriptors[0].entity_id == "sensor.enyaq_range"


def test_registry_adapter_preserves_discovery_metadata() -> None:
    descriptor = discovery_descriptors_from_registry(
        (
            RegistryEntityRecord(
                entity_id="sensor.deye_pv_power",
                name="PV výkon",
                device_name="Deye měnič",
                platform="deye",
                domain="sensor",
                device_class="power",
                state_class="measurement",
                unit_of_measurement="kW",
            ),
        )
    )[0]

    assert descriptor.name == "PV výkon"
    assert descriptor.device_name == "Deye měnič"
    assert descriptor.integration == "deye"
    assert descriptor.device_class == "power"
    assert descriptor.state_class == "measurement"
    assert descriptor.unit == "kW"
