from custom_components.frakon_energy.entity_discovery import (
    EntityDescriptor,
    EntityRole,
    discover_existing_entities,
)
from custom_components.frakon_energy.technology_profile import HouseTechnology


def test_heat_pump_reuses_power_and_energy_entities() -> None:
    entities = (
        EntityDescriptor(
            "sensor.heat_pump_power",
            name="Tepelné čerpadlo příkon",
            device_class="power",
            unit="kW",
        ),
        EntityDescriptor(
            "sensor.heat_pump_energy",
            name="Tepelné čerpadlo energie",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
        ),
    )

    result = discover_existing_entities(HouseTechnology.HEAT_PUMP, entities)

    assert result[EntityRole.POWER][0].entity_id == "sensor.heat_pump_power"
    assert result[EntityRole.ENERGY_TOTAL][0].entity_id == "sensor.heat_pump_energy"


def test_smart_meter_separates_grid_import_and_export() -> None:
    entities = (
        EntityDescriptor(
            "sensor.grid_import_power",
            name="Grid import odběr",
            device_class="power",
            unit="kW",
        ),
        EntityDescriptor(
            "sensor.grid_export_power",
            name="Grid export přetok",
            device_class="power",
            unit="kW",
        ),
    )

    result = discover_existing_entities(HouseTechnology.SMART_METER, entities)

    assert result[EntityRole.GRID_IMPORT][0].entity_id == "sensor.grid_import_power"
    assert result[EntityRole.GRID_EXPORT][0].entity_id == "sensor.grid_export_power"


def test_energy_export_can_reuse_export_and_total_energy() -> None:
    entities = (
        EntityDescriptor(
            "sensor.pretok_do_site",
            name="Přetok do sítě",
            device_class="power",
            unit="W",
        ),
        EntityDescriptor(
            "sensor.export_energy_total",
            name="Export energy total",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
        ),
    )

    result = discover_existing_entities(HouseTechnology.ENERGY_EXPORT, entities)

    assert result[EntityRole.GRID_EXPORT][0].entity_id == "sensor.pretok_do_site"
    assert result[EntityRole.ENERGY_TOTAL][0].entity_id == "sensor.export_energy_total"


def test_submeters_support_power_and_total_energy() -> None:
    entities = (
        EntityDescriptor(
            "sensor.workshop_power",
            name="Dílna výkon",
            device_class="power",
            unit="W",
        ),
        EntityDescriptor(
            "sensor.workshop_energy",
            name="Dílna energie",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
        ),
    )

    result = discover_existing_entities(HouseTechnology.SUBMETERS, entities)

    assert result[EntityRole.POWER][0].entity_id == "sensor.workshop_power"
    assert result[EntityRole.ENERGY_TOTAL][0].entity_id == "sensor.workshop_energy"
