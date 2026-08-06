from custom_components.frakon_energy.entity_discovery_runtime import EntityDiscoveryRuntime
from custom_components.frakon_energy.entity_discovery_websocket import (
    COMMAND_GET,
    COMMAND_REMOVE,
    COMMAND_SAVE,
)
from custom_components.frakon_energy.ha_entity_registry import RegistryEntityRecord
from custom_components.frakon_energy.technology_profile import (
    HouseTechnology,
    HouseTechnologyProfile,
    TechnologySelection,
)


def profile() -> HouseTechnologyProfile:
    return HouseTechnologyProfile(
        technologies=tuple(
            TechnologySelection(
                technology=item,
                enabled=item == HouseTechnology.ELECTRIC_VEHICLE,
            )
            for item in HouseTechnology
        )
    )


def runtime():
    state = {"options": {"other": "preserved"}}

    def update(options):
        state["options"] = dict(options)

    service = EntityDiscoveryRuntime(
        profile_provider=profile,
        registry_provider=lambda: (
            RegistryEntityRecord(
                entity_id="sensor.enyaq_battery",
                name="Enyaq battery",
                device_class="battery",
                unit_of_measurement="%",
            ),
        ),
        options_provider=lambda: state["options"],
        options_updater=update,
    )
    return service, state


def test_get_returns_live_snapshot() -> None:
    service, _ = runtime()
    payload = service.dispatch(COMMAND_GET)
    assert payload["scanned_entities"] == 1
    assert payload["technologies"][0]["technology"] == "electric_vehicle"


def test_save_and_remove_persist_mapping() -> None:
    service, state = runtime()
    saved = service.dispatch(
        COMMAND_SAVE,
        {
            "technology": "electric_vehicle",
            "role": "battery_level",
            "entity_id": "sensor.enyaq_battery",
        },
        is_admin=True,
    )
    role = saved["technologies"][0]["roles"][0]
    assert role["configured"] is True
    assert state["options"]["other"] == "preserved"

    removed = service.dispatch(
        COMMAND_REMOVE,
        {"technology": "electric_vehicle", "role": "battery_level"},
        is_admin=True,
    )
    assert removed["technologies"][0]["roles"][0]["configured"] is False


def test_mutations_require_admin() -> None:
    service, _ = runtime()
    try:
        service.dispatch(
            COMMAND_SAVE,
            {
                "technology": "electric_vehicle",
                "role": "battery_level",
                "entity_id": "sensor.enyaq_battery",
            },
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("save must require administrator privileges")
