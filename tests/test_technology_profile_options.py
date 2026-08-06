from custom_components.frakon_energy.technology_profile import HouseTechnology
from custom_components.frakon_energy.technology_profile_options import (
    technology_profile_from_options,
)


def test_missing_options_return_complete_disabled_profile() -> None:
    profile = technology_profile_from_options({})

    assert len(profile.technologies) == len(HouseTechnology)
    assert profile.enabled() == ()


def test_options_enable_technology_and_preserve_unique_entities() -> None:
    profile = technology_profile_from_options(
        {
            "technologies": [
                {
                    "id": "electric_vehicle",
                    "enabled": True,
                    "entity_ids": [
                        "sensor.enyaq_battery",
                        "sensor.enyaq_range",
                        "sensor.enyaq_battery",
                        "invalid",
                    ],
                }
            ]
        }
    )

    selected = next(
        item for item in profile.technologies
        if item.technology is HouseTechnology.ELECTRIC_VEHICLE
    )
    assert selected.enabled is True
    assert selected.entity_ids == (
        "sensor.enyaq_battery",
        "sensor.enyaq_range",
    )


def test_unknown_and_malformed_records_are_ignored() -> None:
    profile = technology_profile_from_options(
        {
            "technologies": [
                {"id": "not-supported", "enabled": True},
                "broken",
                {"id": "wallbox", "enabled": True, "entity_ids": "sensor.bad"},
            ]
        }
    )

    assert profile.has(HouseTechnology.WALLBOX)
    wallbox = next(
        item for item in profile.technologies
        if item.technology is HouseTechnology.WALLBOX
    )
    assert wallbox.entity_ids == ()
