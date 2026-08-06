from custom_components.frakon_energy.entity_assignment import EntityAssignment
from custom_components.frakon_energy.entity_discovery import EntityRole
from custom_components.frakon_energy.entity_discovery_service import (
    build_runtime_entity_discovery_snapshot,
)
from custom_components.frakon_energy.ha_entity_registry import RegistryEntityRecord
from custom_components.frakon_energy.technology_profile import (
    HouseTechnology,
    HouseTechnologyProfile,
    TechnologySelection,
)


def profile_with(*technologies: HouseTechnology) -> HouseTechnologyProfile:
    enabled = set(technologies)
    return HouseTechnologyProfile(
        technologies=tuple(
            TechnologySelection(technology=item, enabled=item in enabled)
            for item in HouseTechnology
        )
    )


def test_runtime_snapshot_builds_ev_recommendation_from_registry() -> None:
    snapshot = build_runtime_entity_discovery_snapshot(
        profile=profile_with(HouseTechnology.ELECTRIC_VEHICLE),
        registry_records=(
            RegistryEntityRecord(
                entity_id="sensor.enyaq_battery",
                name="Enyaq battery SOC",
                device_name="Škoda Enyaq",
                platform="myskoda",
                device_class="battery",
                unit_of_measurement="%",
            ),
        ),
    ).as_dict()

    ev = snapshot["technologies"][0]
    battery = next(item for item in ev["roles"] if item["role"] == "battery_level")
    assert battery["candidates"][0]["entity_id"] == "sensor.enyaq_battery"
    assert snapshot["scanned_entities"] == 1
    assert snapshot["usable_entities"] == 1


def test_runtime_snapshot_preserves_confirmed_assignment() -> None:
    snapshot = build_runtime_entity_discovery_snapshot(
        profile=profile_with(HouseTechnology.WALLBOX),
        registry_records=(
            RegistryEntityRecord(
                entity_id="sensor.wallbox_power",
                name="Wallbox charging power",
                device_class="power",
                unit_of_measurement="kW",
            ),
        ),
        assignments=(
            EntityAssignment(
                technology=HouseTechnology.WALLBOX,
                role=EntityRole.POWER,
                entity_id="sensor.wallbox_power",
                confirmed=True,
            ),
        ),
    ).as_dict()

    wallbox = snapshot["technologies"][0]
    power = next(item for item in wallbox["roles"] if item["role"] == "power")
    assert power["selected_entity_id"] == "sensor.wallbox_power"
    assert power["configured"] is True


def test_unavailable_entities_are_optional_for_diagnostics() -> None:
    record = RegistryEntityRecord(
        entity_id="sensor.enyaq_range",
        name="Enyaq range",
        device_class="distance",
        unit_of_measurement="km",
        unavailable=True,
    )

    normal = build_runtime_entity_discovery_snapshot(
        profile=profile_with(HouseTechnology.ELECTRIC_VEHICLE),
        registry_records=(record,),
    ).as_dict()
    diagnostic = build_runtime_entity_discovery_snapshot(
        profile=profile_with(HouseTechnology.ELECTRIC_VEHICLE),
        registry_records=(record,),
        include_unavailable=True,
    ).as_dict()

    assert normal["usable_entities"] == 0
    assert diagnostic["usable_entities"] == 1
