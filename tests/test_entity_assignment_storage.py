from custom_components.frakon_energy.entity_assignment import EntityAssignment
from custom_components.frakon_energy.entity_assignment_storage import (
    OPTIONS_KEY_ENTITY_ASSIGNMENTS,
    load_entity_assignment_storage,
    remove_stale_entity_assignments,
    store_entity_assignments,
)
from custom_components.frakon_energy.entity_discovery import EntityRole
from custom_components.frakon_energy.technology_profile import HouseTechnology


def assignment(
    technology: HouseTechnology,
    role: EntityRole,
    entity_id: str,
) -> EntityAssignment:
    return EntityAssignment(
        technology=technology,
        role=role,
        entity_id=entity_id,
        confirmed=True,
    )


def test_store_and_load_entity_assignments_round_trip() -> None:
    options = store_entity_assignments(
        {"unrelated": "preserved"},
        (
            assignment(
                HouseTechnology.ELECTRIC_VEHICLE,
                EntityRole.BATTERY_LEVEL,
                "sensor.enyaq_battery",
            ),
        ),
    )

    assert options["unrelated"] == "preserved"
    assert options[OPTIONS_KEY_ENTITY_ASSIGNMENTS]["version"] == 1

    loaded = load_entity_assignment_storage(options)
    assert loaded.assignments[0].entity_id == "sensor.enyaq_battery"
    assert loaded.assignments[0].confirmed is True


def test_load_ignores_malformed_and_unknown_records() -> None:
    loaded = load_entity_assignment_storage(
        {
            OPTIONS_KEY_ENTITY_ASSIGNMENTS: {
                "version": 1,
                "items": [
                    {
                        "technology": "electric_vehicle",
                        "role": "battery_level",
                        "entity_id": "sensor.enyaq_battery",
                    },
                    {
                        "technology": "unknown",
                        "role": "battery_level",
                        "entity_id": "sensor.invalid_technology",
                    },
                    {
                        "technology": "electric_vehicle",
                        "role": "range",
                        "entity_id": "invalid",
                    },
                ],
            }
        }
    )

    assert [item.entity_id for item in loaded.assignments] == ["sensor.enyaq_battery"]


def test_duplicate_technology_role_keeps_first_mapping() -> None:
    loaded = load_entity_assignment_storage(
        {
            OPTIONS_KEY_ENTITY_ASSIGNMENTS: {
                "items": [
                    {
                        "technology": "wallbox",
                        "role": "power",
                        "entity_id": "sensor.wallbox_power_primary",
                    },
                    {
                        "technology": "wallbox",
                        "role": "power",
                        "entity_id": "sensor.wallbox_power_duplicate",
                    },
                ]
            }
        }
    )

    assert [item.entity_id for item in loaded.assignments] == [
        "sensor.wallbox_power_primary"
    ]


def test_remove_stale_assignments_preserves_existing_entities() -> None:
    assignments = (
        assignment(
            HouseTechnology.ELECTRIC_VEHICLE,
            EntityRole.BATTERY_LEVEL,
            "sensor.enyaq_battery",
        ),
        assignment(
            HouseTechnology.WALLBOX,
            EntityRole.POWER,
            "sensor.removed_wallbox",
        ),
    )

    cleaned = remove_stale_entity_assignments(assignments, {"sensor.enyaq_battery"})
    assert [item.entity_id for item in cleaned] == ["sensor.enyaq_battery"]


def test_empty_or_legacy_options_load_safely() -> None:
    assert load_entity_assignment_storage(None).assignments == ()
    assert load_entity_assignment_storage({OPTIONS_KEY_ENTITY_ASSIGNMENTS: []}).assignments == ()
