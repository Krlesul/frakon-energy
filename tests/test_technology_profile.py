import pytest

from custom_components.frakon_energy.technology_profile import (
    HouseTechnology,
    HouseTechnologyProfile,
    TechnologySelection,
    default_technology_profile,
    technology_profile_payload,
)


def test_default_profile_keeps_optional_technologies_hidden() -> None:
    profile = default_technology_profile()

    assert profile.enabled() == ()
    assert profile.visible_modules() == ("grid", "consumption", "billing")


def test_enabled_technologies_control_visible_modules() -> None:
    profile = HouseTechnologyProfile(
        technologies=(
            TechnologySelection(HouseTechnology.PHOTOVOLTAICS, enabled=True),
            TechnologySelection(HouseTechnology.HOME_BATTERY, enabled=True),
            TechnologySelection(HouseTechnology.ELECTRIC_VEHICLE, enabled=True),
            TechnologySelection(HouseTechnology.WALLBOX, enabled=False),
            TechnologySelection(HouseTechnology.HDO, enabled=True),
        )
    )

    assert profile.has(HouseTechnology.PHOTOVOLTAICS) is True
    assert profile.has(HouseTechnology.WALLBOX) is False
    assert profile.visible_modules() == (
        "grid",
        "consumption",
        "billing",
        "photovoltaics",
        "battery",
        "electric_vehicle",
        "hdo",
    )


def test_payload_contains_labels_entities_and_enabled_ids() -> None:
    profile = HouseTechnologyProfile(
        technologies=(
            TechnologySelection(
                HouseTechnology.ELECTRIC_VEHICLE,
                enabled=True,
                entity_ids=("sensor.enyaq_soc", "sensor.enyaq_charging_power"),
            ),
        )
    )

    payload = technology_profile_payload(profile)

    assert payload["enabled"] == ["electric_vehicle"]
    assert payload["visible_modules"][-1] == "electric_vehicle"
    assert payload["technologies"][0]["label"] == "Elektromobil"
    assert payload["technologies"][0]["entity_ids"] == [
        "sensor.enyaq_soc",
        "sensor.enyaq_charging_power",
    ]


def test_duplicate_technologies_are_rejected() -> None:
    with pytest.raises(ValueError):
        HouseTechnologyProfile(
            technologies=(
                TechnologySelection(HouseTechnology.HOME_BATTERY),
                TechnologySelection(HouseTechnology.HOME_BATTERY),
            )
        )


def test_duplicate_or_invalid_entity_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        TechnologySelection(
            HouseTechnology.WALLBOX,
            entity_ids=("sensor.power", "sensor.power"),
        )

    with pytest.raises(ValueError):
        TechnologySelection(HouseTechnology.WALLBOX, entity_ids=("invalid",))
