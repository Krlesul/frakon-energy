from custom_components.frakon_energy.entity_discovery_lifecycle import (
    EntityDiscoveryRuntimeRegistry,
)
from custom_components.frakon_energy.entity_discovery_setup import (
    setup_entity_discovery_runtime,
    unload_entity_discovery_runtime,
)
from custom_components.frakon_energy.ha_entity_registry import RegistryEntityRecord
from custom_components.frakon_energy.technology_profile import HouseTechnologyProfile


def test_setup_registers_runtime_and_binds_dependencies() -> None:
    runtime_registry = EntityDiscoveryRuntimeRegistry()
    profile = HouseTechnologyProfile()
    records = (
        RegistryEntityRecord(
            entity_id="sensor.enyaq_battery",
            name="Enyaq battery",
            device_class="battery",
            unit_of_measurement="%",
        ),
    )
    options = {"preserved": True}
    updates: list[dict[str, object]] = []

    runtime = setup_entity_discovery_runtime(
        entry_id="entry-1",
        runtime_registry=runtime_registry,
        profile_provider=lambda: profile,
        registry_provider=lambda: records,
        options_provider=lambda: options,
        options_updater=lambda value: updates.append(dict(value)),
    )

    assert runtime_registry.get("entry-1") is runtime
    assert runtime.profile_provider() is profile
    assert tuple(runtime.registry_provider()) == records
    runtime.options_updater({"changed": True})
    assert updates == [{"changed": True}]


def test_setup_replaces_existing_runtime_for_same_entry() -> None:
    runtime_registry = EntityDiscoveryRuntimeRegistry()

    first = setup_entity_discovery_runtime(
        entry_id="entry-1",
        runtime_registry=runtime_registry,
        profile_provider=HouseTechnologyProfile,
        registry_provider=tuple,
        options_provider=dict,
        options_updater=lambda _value: None,
    )
    second = setup_entity_discovery_runtime(
        entry_id="entry-1",
        runtime_registry=runtime_registry,
        profile_provider=HouseTechnologyProfile,
        registry_provider=tuple,
        options_provider=dict,
        options_updater=lambda _value: None,
    )

    assert second is not first
    assert runtime_registry.get("entry-1") is second
    assert runtime_registry.as_frontend_summary()["count"] == 1


def test_unload_removes_only_requested_runtime() -> None:
    runtime_registry = EntityDiscoveryRuntimeRegistry()
    for entry_id in ("entry-1", "entry-2"):
        setup_entity_discovery_runtime(
            entry_id=entry_id,
            runtime_registry=runtime_registry,
            profile_provider=HouseTechnologyProfile,
            registry_provider=tuple,
            options_provider=dict,
            options_updater=lambda _value: None,
        )

    assert unload_entity_discovery_runtime(
        entry_id="entry-1", runtime_registry=runtime_registry
    ) is True
    assert runtime_registry.as_frontend_summary() == {
        "entry_ids": ["entry-2"],
        "count": 1,
    }
    assert unload_entity_discovery_runtime(
        entry_id="entry-1", runtime_registry=runtime_registry
    ) is False
