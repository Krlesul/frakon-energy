from custom_components.frakon_energy.entity_discovery_runtime import EntityDiscoveryRuntime
from custom_components.frakon_energy.entity_discovery_runtime_factory import (
    build_entity_discovery_runtime,
)
from custom_components.frakon_energy.ha_entity_registry import RegistryEntityRecord
from custom_components.frakon_energy.technology_profile import HouseTechnologyProfile


def test_factory_binds_runtime_dependencies() -> None:
    options = {"unrelated": "preserved"}
    profile = HouseTechnologyProfile()
    records = (
        RegistryEntityRecord(
            entity_id="sensor.example_power",
            name="Example power",
            device_class="power",
            unit_of_measurement="W",
        ),
    )
    updated: list[dict[str, object]] = []

    runtime = build_entity_discovery_runtime(
        profile_provider=lambda: profile,
        registry_provider=lambda: records,
        options_provider=lambda: options,
        options_updater=lambda value: updated.append(dict(value)),
    )

    assert isinstance(runtime, EntityDiscoveryRuntime)
    assert runtime.profile_provider() is profile
    assert tuple(runtime.registry_provider()) == records
    assert runtime.options_provider()["unrelated"] == "preserved"

    runtime.options_updater({"changed": True})
    assert updated == [{"changed": True}]
