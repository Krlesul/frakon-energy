from custom_components.frakon_energy.entity_discovery import (
    EntityDescriptor,
    EntityRole,
    discover_existing_entities,
)
from custom_components.frakon_energy.technology_profile import HouseTechnology


def test_ev_discovery_prefers_existing_battery_sensor() -> None:
    entities = (
        EntityDescriptor(
            entity_id="sensor.skoda_enyaq_battery_level",
            name="Enyaq battery SOC",
            device_name="Škoda Enyaq",
            integration="myskoda",
            device_class="battery",
            unit="%",
        ),
        EntityDescriptor(
            entity_id="sensor.phone_battery",
            name="Phone battery",
            device_name="iPhone",
            device_class="battery",
            unit="%",
        ),
    )
    matches = discover_existing_entities(HouseTechnology.ELECTRIC_VEHICLE, entities)
    assert matches[EntityRole.BATTERY_LEVEL][0].entity_id == "sensor.skoda_enyaq_battery_level"
    assert matches[EntityRole.BATTERY_LEVEL][0].confidence == 100


def test_power_and_energy_roles_use_home_assistant_metadata() -> None:
    entities = (
        EntityDescriptor(
            entity_id="sensor.wallbox_charging_power",
            name="Wallbox charging power",
            device_name="Wallbox",
            device_class="power",
            unit="kW",
        ),
        EntityDescriptor(
            entity_id="sensor.wallbox_energy_total",
            name="Wallbox energy total",
            device_name="Wallbox",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
        ),
    )
    matches = discover_existing_entities(HouseTechnology.WALLBOX, entities)
    assert matches[EntityRole.POWER][0].entity_id == "sensor.wallbox_charging_power"
    assert matches[EntityRole.ENERGY_TOTAL][0].entity_id == "sensor.wallbox_energy_total"
    assert matches[EntityRole.ENERGY_TOTAL][0].confidence == 100


def test_low_confidence_match_requires_user_confirmation() -> None:
    entity = EntityDescriptor(
        entity_id="binary_sensor.car_charging",
        name="Car charging",
        domain="binary_sensor",
    )
    match = discover_existing_entities(
        HouseTechnology.ELECTRIC_VEHICLE,
        (entity,),
    )[EntityRole.CHARGING_STATE][0]
    assert match.confidence == 100
    assert match.requires_confirmation is False


def test_unrelated_entities_are_not_offered() -> None:
    entity = EntityDescriptor(
        entity_id="sensor.living_room_temperature",
        name="Living room temperature",
        device_class="temperature",
        unit="°C",
    )
    matches = discover_existing_entities(HouseTechnology.ELECTRIC_VEHICLE, (entity,))
    assert all(not role_matches for role_matches in matches.values())


def test_pv_import_and_export_are_detected_separately() -> None:
    entities = (
        EntityDescriptor(
            entity_id="sensor.deye_grid_import",
            name="Grid import",
            device_class="energy",
            unit="kWh",
        ),
        EntityDescriptor(
            entity_id="sensor.deye_grid_export",
            name="Grid export",
            device_class="energy",
            unit="kWh",
        ),
    )
    matches = discover_existing_entities(HouseTechnology.PHOTOVOLTAICS, entities)
    assert matches[EntityRole.GRID_IMPORT][0].entity_id == "sensor.deye_grid_import"
    assert matches[EntityRole.GRID_EXPORT][0].entity_id == "sensor.deye_grid_export"


def test_smart_meter_phase_currents_require_explicit_phase_identity() -> None:
    entities = (
        EntityDescriptor(
            entity_id="sensor.meter_current_l1",
            name="Grid Current L1",
            device_class="current",
            unit="A",
        ),
        EntityDescriptor(
            entity_id="sensor.meter_current_l2",
            name="Grid Current L2",
            device_class="current",
            unit="A",
        ),
        EntityDescriptor(
            entity_id="sensor.meter_current_l3",
            name="Grid Current L3",
            device_class="current",
            unit="A",
        ),
        EntityDescriptor(
            entity_id="sensor.meter_current",
            name="Grid Current",
            device_class="current",
            unit="A",
        ),
    )

    matches = discover_existing_entities(HouseTechnology.SMART_METER, entities)

    assert matches[EntityRole.GRID_CURRENT_L1][0].entity_id == "sensor.meter_current_l1"
    assert matches[EntityRole.GRID_CURRENT_L2][0].entity_id == "sensor.meter_current_l2"
    assert matches[EntityRole.GRID_CURRENT_L3][0].entity_id == "sensor.meter_current_l3"
    assert matches[EntityRole.GRID_CURRENT_L1][0].confidence == 100
    assert all(item.entity_id != "sensor.meter_current" for role in (
        EntityRole.GRID_CURRENT_L1,
        EntityRole.GRID_CURRENT_L2,
        EntityRole.GRID_CURRENT_L3,
    ) for item in matches[role])


def test_phase_marker_for_other_phase_is_not_cross_assigned() -> None:
    entity = EntityDescriptor(
        entity_id="sensor.phase_2_current",
        name="Phase 2 current",
        device_class="current",
        unit="A",
    )
    matches = discover_existing_entities(HouseTechnology.SMART_METER, (entity,))
    assert matches[EntityRole.GRID_CURRENT_L1] == ()
    assert matches[EntityRole.GRID_CURRENT_L2][0].entity_id == "sensor.phase_2_current"
    assert matches[EntityRole.GRID_CURRENT_L3] == ()