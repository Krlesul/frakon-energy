from custom_components.frakon_energy.dashboard_plan import (
    available_dashboard_modules,
    dashboard_plan_payload,
)
from custom_components.frakon_energy.technology_profile import (
    HouseTechnology,
    HouseTechnologyProfile,
    TechnologySelection,
    default_technology_profile,
)


def profile_with(*technologies: HouseTechnology) -> HouseTechnologyProfile:
    enabled = set(technologies)
    return HouseTechnologyProfile(
        technologies=tuple(
            TechnologySelection(technology=item, enabled=item in enabled)
            for item in HouseTechnology
        )
    )


def test_default_profile_exposes_only_core_modules() -> None:
    ids = [module.id for module in available_dashboard_modules(default_technology_profile())]

    assert ids == ["energy_summary", "current_tariff", "billing"]


def test_selected_technologies_enable_matching_modules() -> None:
    profile = profile_with(
        HouseTechnology.HDO,
        HouseTechnology.PHOTOVOLTAICS,
        HouseTechnology.HOME_BATTERY,
        HouseTechnology.ELECTRIC_VEHICLE,
        HouseTechnology.WALLBOX,
    )

    ids = [module.id for module in available_dashboard_modules(profile)]

    assert "hdo_timeline" in ids
    assert "photovoltaics" in ids
    assert "home_battery" in ids
    assert "electric_vehicle" in ids
    assert "wallbox" in ids
    assert "smart_charging" in ids
    assert "heat_pump" not in ids
    assert "energy_export" not in ids


def test_combined_modules_require_all_dependencies() -> None:
    only_ev = profile_with(HouseTechnology.ELECTRIC_VEHICLE)
    only_wallbox = profile_with(HouseTechnology.WALLBOX)
    both = profile_with(HouseTechnology.ELECTRIC_VEHICLE, HouseTechnology.WALLBOX)

    assert "smart_charging" not in [module.id for module in available_dashboard_modules(only_ev)]
    assert "smart_charging" not in [module.id for module in available_dashboard_modules(only_wallbox)]
    assert "smart_charging" in [module.id for module in available_dashboard_modules(both)]


def test_export_requires_photovoltaics_and_export_metering() -> None:
    export_only = profile_with(HouseTechnology.ENERGY_EXPORT)
    complete = profile_with(HouseTechnology.PHOTOVOLTAICS, HouseTechnology.ENERGY_EXPORT)

    assert "energy_export" not in [module.id for module in available_dashboard_modules(export_only)]
    assert "energy_export" in [module.id for module in available_dashboard_modules(complete)]


def test_payload_contains_frontend_safe_module_metadata() -> None:
    payload = dashboard_plan_payload(profile_with(HouseTechnology.HDO, HouseTechnology.HEAT_PUMP))

    assert payload["module_ids"] == [
        "energy_summary",
        "current_tariff",
        "billing",
        "hdo_timeline",
        "heat_pump",
    ]
    assert "core" in payload["categories"]
    assert "tariff" in payload["categories"]
    assert "heating" in payload["categories"]
    hdo = next(item for item in payload["modules"] if item["id"] == "hdo_timeline")
    assert hdo["required_technologies"] == ["hdo"]
