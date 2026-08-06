import pytest

from custom_components.frakon_energy.entity_discovery import EntityRole
from custom_components.frakon_energy.entity_discovery_ws import (
    EntityAssignmentStore,
    WS_GET_ENTITY_DISCOVERY,
    WS_REMOVE_ENTITY_ASSIGNMENT,
    WS_RESCAN_ENTITY_DISCOVERY,
    WS_SAVE_ENTITY_ASSIGNMENT,
    assignments_payload,
    websocket_command_contract,
)
from custom_components.frakon_energy.technology_profile import HouseTechnology


def test_store_saves_confirmed_existing_entity_assignment() -> None:
    store = EntityAssignmentStore()

    assignment = store.save(
        technology=HouseTechnology.ELECTRIC_VEHICLE,
        role=EntityRole.BATTERY_LEVEL,
        entity_id="sensor.enyaq_battery",
    )

    assert assignment.confirmed is True
    assert assignments_payload(store.all()) == [
        {
            "technology": "electric_vehicle",
            "role": "battery_level",
            "entity_id": "sensor.enyaq_battery",
            "confirmed": True,
        }
    ]


def test_store_replaces_only_same_technology_role() -> None:
    store = EntityAssignmentStore()
    store.save(
        technology=HouseTechnology.ELECTRIC_VEHICLE,
        role=EntityRole.BATTERY_LEVEL,
        entity_id="sensor.enyaq_battery_old",
    )
    store.save(
        technology=HouseTechnology.ELECTRIC_VEHICLE,
        role=EntityRole.RANGE,
        entity_id="sensor.enyaq_range",
    )
    store.save(
        technology=HouseTechnology.ELECTRIC_VEHICLE,
        role=EntityRole.BATTERY_LEVEL,
        entity_id="sensor.enyaq_battery_new",
    )

    payload = assignments_payload(store.all())
    assert len(payload) == 2
    assert any(item["entity_id"] == "sensor.enyaq_battery_new" for item in payload)
    assert any(item["entity_id"] == "sensor.enyaq_range" for item in payload)


def test_remove_assignment_reports_change() -> None:
    store = EntityAssignmentStore()
    store.save(
        technology="wallbox",
        role="power",
        entity_id="sensor.wallbox_power",
    )

    assert store.remove(technology="wallbox", role="power") is True
    assert store.remove(technology="wallbox", role="power") is False


def test_invalid_entity_id_is_rejected() -> None:
    store = EntityAssignmentStore()

    with pytest.raises(ValueError):
        store.save(
            technology="electric_vehicle",
            role="battery_level",
            entity_id="invalid",
        )


def test_websocket_contract_separates_reads_and_admin_writes() -> None:
    commands = {item["type"]: item for item in websocket_command_contract()}

    assert commands[WS_GET_ENTITY_DISCOVERY]["admin_required"] is False
    assert commands[WS_GET_ENTITY_DISCOVERY]["mutates"] is False
    assert commands[WS_SAVE_ENTITY_ASSIGNMENT]["admin_required"] is True
    assert commands[WS_SAVE_ENTITY_ASSIGNMENT]["mutates"] is True
    assert commands[WS_REMOVE_ENTITY_ASSIGNMENT]["admin_required"] is True
    assert commands[WS_RESCAN_ENTITY_DISCOVERY]["admin_required"] is True
