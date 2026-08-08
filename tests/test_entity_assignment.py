from custom_components.frakon_energy.entity_assignment import (
    EntityAssignment,
    build_discovery_results,
    discovery_payload,
)
from custom_components.frakon_energy.entity_discovery import EntityDescriptor, EntityRole
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


def test_discovery_runs_only_for_enabled_technologies() -> None:
    results = build_discovery_results(
        profile_with(HouseTechnology.ELECTRIC_VEHICLE),
        (
            EntityDescriptor(
                entity_id="sensor.enyaq_battery",
                name="Enyaq battery SOC",
                device_class="battery",
                unit="%",
            ),
        ),
    )
    assert [item.technology for item in results] == [HouseTechnology.ELECTRIC_VEHICLE]


def test_payload_contains_ranked_candidates_and_confirmation_state() -> None:
    results = build_discovery_results(
        profile_with(HouseTechnology.ELECTRIC_VEHICLE),
        (
            EntityDescriptor(
                entity_id="sensor.enyaq_battery",
                name="Enyaq battery SOC",
                device_name="Škoda Enyaq",
                device_class="battery",
                unit="%",
            ),
        ),
    )
    payload = discovery_payload(results)
    ev = payload["technologies"][0]
    battery = next(item for item in ev["roles"] if item["role"] == "battery_level")
    assert battery["label"] == "Stav baterie"
    assert battery["configured"] is False
    assert battery["required"] is True
    assert battery["candidates"][0]["entity_id"] == "sensor.enyaq_battery"
    assert battery["candidates"][0]["confidence"] == 100
    assert battery["candidates"][0]["requires_confirmation"] is False


def test_confirmed_assignment_is_exposed_without_creating_duplicate_entity() -> None:
    profile = profile_with(HouseTechnology.WALLBOX)
    results = build_discovery_results(
        profile,
        (
            EntityDescriptor(
                entity_id="sensor.wallbox_power",
                name="Wallbox charging power",
                device_class="power",
                unit="kW",
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
    )
    payload = discovery_payload(results)
    wallbox = payload["technologies"][0]
    power = next(item for item in wallbox["roles"] if item["role"] == "power")
    assert power["selected_entity_id"] == "sensor.wallbox_power"
    assert power["configured"] is True
    assert wallbox["configured_roles"] == 1
    assert wallbox["complete"] is False


def test_unrelated_entities_are_not_exposed_as_candidates() -> None:
    results = build_discovery_results(
        profile_with(HouseTechnology.PHOTOVOLTAICS),
        (
            EntityDescriptor(
                entity_id="sensor.living_room_temperature",
                name="Living room temperature",
                device_class="temperature",
                unit="°C",
            ),
        ),
    )
    payload = discovery_payload(results)
    assert all(not role["candidates"] for role in payload["technologies"][0]["roles"])


def test_optional_phase_currents_do_not_make_smart_meter_incomplete() -> None:
    profile = profile_with(HouseTechnology.SMART_METER)
    required_assignments = (
        EntityAssignment(HouseTechnology.SMART_METER, EntityRole.GRID_IMPORT, "sensor.grid_import", True),
        EntityAssignment(HouseTechnology.SMART_METER, EntityRole.GRID_EXPORT, "sensor.grid_export", True),
        EntityAssignment(HouseTechnology.SMART_METER, EntityRole.ENERGY_TOTAL, "sensor.grid_energy", True),
    )
    results = build_discovery_results(profile, (), assignments=required_assignments)
    smart_meter = discovery_payload(results)["technologies"][0]

    phase_roles = [
        item for item in smart_meter["roles"]
        if item["role"] in {"grid_current_l1", "grid_current_l2", "grid_current_l3"}
    ]
    assert len(phase_roles) == 3
    assert all(item["required"] is False for item in phase_roles)
    assert smart_meter["required_roles"] == 3
    assert smart_meter["configured_required_roles"] == 3
    assert smart_meter["complete"] is True